// raw_slowlog_exporter is a restricted stdio exporter for the tdsql_log_reader
// ForceCommand account. It accepts no paths or shell arguments from SSH.
package main

import (
	"bufio"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const protocol = "raw_slowlog_exporter_v1"
const version = "1.5.3.0"

type sourceConfig struct {
	Paths           []string `json:"paths"`
	StorageIdentity string   `json:"storage_identity"`
}

type exporterConfig struct {
	Sources map[string]sourceConfig `json:"sources"`
}

type cursorRequest struct {
	FileIdentity      string `json:"file_identity"`
	Generation        int    `json:"generation"`
	Offset            int64  `json:"offset"`
	AnchorStartOffset int64  `json:"anchor_start_offset"`
	AnchorLength      int    `json:"anchor_length"`
}

type request struct {
	Op                     string          `json:"op"`
	Protocol               string          `json:"protocol"`
	SourceKey              string          `json:"source_key"`
	MaxBytes               int             `json:"max_bytes"`
	InitialPosition        string          `json:"initial_position"`
	InitialLookbackSeconds int             `json:"initial_lookback_seconds"`
	Timezone               string          `json:"timezone"`
	Cursors                []cursorRequest `json:"cursors"`
}

type fileMeta struct {
	Path         string `json:"-"`
	FileIdentity string `json:"file_identity"`
	FileLabel    string `json:"file_label"`
	FileSize     int64  `json:"file_size"`
	ModifiedAt   string `json:"modified_at"`
}

func emit(value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	_, err = fmt.Println(string(data))
	return err
}

func emitError(code string) {
	// Details stay in sshd/server logs. Protocol output deliberately has no path,
	// host, shell, or configuration values.
	_ = emit(map[string]string{"type": "error", "code": code})
}

func loadConfig(filename string) (exporterConfig, error) {
	var config exporterConfig
	data, err := os.ReadFile(filename)
	if err != nil {
		return config, err
	}
	if err := json.Unmarshal(data, &config); err != nil {
		return config, err
	}
	if len(config.Sources) == 0 {
		return config, errors.New("exporter config has no sources")
	}
	return config, nil
}

func discover(cfg sourceConfig) ([]fileMeta, error) {
	seen := map[string]bool{}
	var files []fileMeta
	for _, pattern := range cfg.Paths {
		matches, err := filepath.Glob(pattern)
		if err != nil {
			return nil, errors.New("invalid configured path pattern")
		}
		for _, path := range matches {
			info, err := os.Stat(path)
			if err != nil || !info.Mode().IsRegular() {
				continue
			}
			identity, err := fileIdentity(info)
			if err != nil || seen[identity] {
				continue
			}
			seen[identity] = true
			files = append(files, fileMeta{
				Path: path, FileIdentity: identity, FileLabel: filepath.Base(path),
				FileSize: info.Size(), ModifiedAt: info.ModTime().UTC().Format(time.RFC3339Nano),
			})
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].Path < files[j].Path })
	return files, nil
}

func readAt(file *os.File, offset int64, size int) ([]byte, error) {
	if size <= 0 {
		return []byte{}, nil
	}
	data := make([]byte, size)
	n, err := file.ReadAt(data, offset)
	if err != nil && err != io.EOF {
		return nil, err
	}
	return data[:n], nil
}

func cursorMap(items []cursorRequest) map[string]cursorRequest {
	result := make(map[string]cursorRequest, len(items))
	for _, item := range items {
		result[item.FileIdentity] = item
	}
	return result
}

func inspectFormat(files []fileMeta) map[string]any {
	// Probe 只返回固定布尔签名，不返回任意日志行或 SQL。读取上限防止一次
	// Probe 意外扫描大文件；正式启用前需按运行手册写入受控验证样本。
	signature := map[string]any{
		"parser_profile":    "tdsql_mysql_slowlog_v1",
		"time_header":       false,
		"query_time_header": false,
	}
	for _, meta := range files {
		file, err := os.Open(meta.Path)
		if err != nil {
			continue
		}
		reader := bufio.NewReaderSize(io.LimitReader(file, 1024*1024), 64*1024)
		for {
			line, readErr := reader.ReadString('\n')
			if strings.HasPrefix(line, "# Time:") {
				signature["time_header"] = true
			}
			if strings.HasPrefix(line, "# Query_time:") {
				signature["query_time_header"] = true
			}
			if readErr != nil {
				break
			}
		}
		file.Close()
		if signature["time_header"].(bool) && signature["query_time_header"].(bool) {
			break
		}
	}
	return signature
}

func parseSlowLogTime(value string, location *time.Location) (time.Time, error) {
	value = strings.TrimSpace(value)
	for _, layout := range []string{
		"2006-01-02T15:04:05.999999999", "2006-01-02T15:04:05",
		"2006-01-02 15:04:05.999999999", "2006-01-02 15:04:05",
		"060102 15:04:05",
	} {
		if parsed, err := time.ParseInLocation(layout, value, location); err == nil {
			return parsed, nil
		}
	}
	return time.Time{}, errors.New("unsupported slow-log time")
}

// initialLookbackOffset returns the first complete record boundary whose
// `# Time` is within the requested time window.  It reads only the configured
// server-side file and never emits its path or contents in error output.
// The scan is used only while the platform has no cursor; subsequent pulls are
// incremental and bounded by MaxBytes.
func initialLookbackOffset(path string, lookbackSeconds int, timezone string, now time.Time) (int64, error) {
	if lookbackSeconds < 60 || lookbackSeconds > 86400 {
		return 0, errors.New("initial lookback out of range")
	}
	location, err := time.LoadLocation(timezone)
	if err != nil {
		return 0, errors.New("invalid timezone")
	}
	file, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer file.Close()

	cutoff := now.In(location).Add(-time.Duration(lookbackSeconds) * time.Second)
	reader := bufio.NewReaderSize(file, 64*1024)
	var offset int64
	var sawTime bool
	for {
		line, readErr := reader.ReadString('\n')
		if len(line) > 0 {
			lineOffset := offset
			offset += int64(len(line))
			if strings.HasPrefix(line, "# Time:") {
				sawTime = true
				parsed, parseErr := parseSlowLogTime(strings.TrimPrefix(line, "# Time:"), location)
				if parseErr != nil {
					return 0, parseErr
				}
				if !parsed.Before(cutoff) {
					return lineOffset, nil
				}
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return 0, readErr
		}
	}
	if !sawTime && offset > 0 {
		return 0, errors.New("slow-log time marker is absent")
	}
	// No matching record means no event belongs to the requested initial
	// window.  Tail the file, rather than silently importing older history.
	return offset, nil
}

func serve(req request, config exporterConfig) error {
	if req.Protocol != protocol {
		return errors.New("protocol mismatch")
	}
	source, ok := config.Sources[req.SourceKey]
	if !ok {
		return errors.New("source key is not configured")
	}
	files, err := discover(source)
	if err != nil {
		return err
	}
	switch req.Op {
	case "version":
		return emit(map[string]string{"type": "version", "protocol": protocol, "version": version})
	case "probe":
		return emit(map[string]any{"type": "probe", "protocol": protocol, "version": version,
			"source_key": req.SourceKey, "storage_identity": source.StorageIdentity, "files": files,
			"format_signature": inspectFormat(files)})
	case "pull":
		maxBytes := req.MaxBytes
		if maxBytes < 1 || maxBytes > 64*1024*1024 {
			return errors.New("max bytes out of range")
		}
		cursors := cursorMap(req.Cursors)
		remaining := maxBytes
		if req.InitialPosition != "tail" && req.InitialPosition != "lookback" {
			return errors.New("initial position is invalid")
		}
		for _, meta := range files {
			if remaining <= 0 {
				break
			}
			cursor, exists := cursors[meta.FileIdentity]
			offset := int64(0)
			if exists {
				offset = cursor.Offset
				if offset < 0 || offset > meta.FileSize {
					return errors.New("cursor reset required")
				}
			} else if req.InitialPosition == "tail" {
				// First enable establishes a safe tail cursor and intentionally does
				// not ingest historical data.
				offset = meta.FileSize
			} else {
				offset, err = initialLookbackOffset(meta.Path, req.InitialLookbackSeconds, req.Timezone, time.Now())
				if err != nil {
					return err
				}
			}
			file, err := os.Open(meta.Path)
			if err != nil {
				return err
			}
			preAnchor := []byte{}
			if exists && cursor.AnchorLength > 0 {
				preAnchor, err = readAt(file, cursor.AnchorStartOffset, cursor.AnchorLength)
				if err != nil {
					file.Close()
					return err
				}
			}
			data, err := readAt(file, offset, remaining)
			if err != nil {
				file.Close()
				return err
			}
			nextOffset := offset + int64(len(data))
			postAnchorSize := int64(64)
			if nextOffset < postAnchorSize {
				postAnchorSize = nextOffset
			}
			postAnchor, err := readAt(file, nextOffset-postAnchorSize, int(postAnchorSize))
			file.Close()
			if err != nil {
				return err
			}
			message := map[string]any{"type": "chunk", "protocol": protocol, "source_key": req.SourceKey,
				"file_identity": meta.FileIdentity,
				"file_label":    meta.FileLabel, "file_size": meta.FileSize, "offset": offset,
				"next_offset": nextOffset, "eof": nextOffset >= meta.FileSize,
				"data_base64":        base64.StdEncoding.EncodeToString(data),
				"post_anchor_base64": base64.StdEncoding.EncodeToString(postAnchor),
				"pre_anchor_base64":  base64.StdEncoding.EncodeToString(preAnchor)}
			if err := emit(message); err != nil {
				return err
			}
			remaining -= len(data)
		}
		return nil
	default:
		return errors.New("unsupported operation")
	}
}

func main() {
	configPath := flag.String("config", "/etc/tdsql-sqlcheck/raw-slowlog-exporter.json", "root-owned exporter configuration")
	stdio := flag.Bool("stdio", false, "serve one JSON request from stdin")
	showVersion := flag.Bool("version", false, "show exporter version")
	flag.Parse()
	if *showVersion {
		fmt.Printf("raw_slowlog_exporter %s\n", version)
		return
	}
	if !*stdio || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "raw_slowlog_exporter only supports --stdio under ForceCommand")
		os.Exit(2)
	}
	config, err := loadConfig(*configPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "exporter configuration error")
		emitError("configuration_error")
		return
	}
	line, err := bufio.NewReader(io.LimitReader(os.Stdin, 1024*1024)).ReadBytes('\n')
	if err != nil && len(line) == 0 {
		emitError("request_error")
		return
	}
	var req request
	if json.Unmarshal(line, &req) != nil {
		emitError("request_error")
		return
	}
	if err := serve(req, config); err != nil {
		fmt.Fprintln(os.Stderr, "exporter request rejected")
		emitError("operation_error")
	}
}

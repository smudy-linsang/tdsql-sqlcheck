package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestServeTailReadsPostAnchorWithoutError(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "slow.log")
	if err := os.WriteFile(path, []byte("# Time: 2026-08-02T10:00:01\nSELECT 1;\n"), 0600); err != nil {
		t.Fatal(err)
	}
	config := exporterConfig{Sources: map[string]sourceConfig{
		"test": {Paths: []string{path}, StorageIdentity: "test-storage"},
	}}
	err := serve(request{Op: "pull", Protocol: protocol, SourceKey: "test", MaxBytes: 1024, InitialPosition: "tail"}, config)
	if err != nil {
		t.Fatal(err)
	}
}

func TestInitialLookbackOffsetChoosesFirstInWindowRecord(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "slow.log")
	content := "# Time: 2026-08-02T09:50:00\n# Query_time: 1.0 Lock_time: 0 Rows_sent: 1 Rows_examined: 1\nSELECT 1;\n" +
		"# Time: 2026-08-02T10:00:01\n# Query_time: 1.0 Lock_time: 0 Rows_sent: 1 Rows_examined: 1\nSELECT 2;\n"
	if err := os.WriteFile(path, []byte(content), 0600); err != nil {
		t.Fatal(err)
	}
	offset, err := initialLookbackOffset(path, 300, "Asia/Shanghai", time.Date(2026, 8, 2, 10, 3, 0, 0, time.FixedZone("CST", 8*3600)))
	if err != nil {
		t.Fatal(err)
	}
	want := int64(len("# Time: 2026-08-02T09:50:00\n# Query_time: 1.0 Lock_time: 0 Rows_sent: 1 Rows_examined: 1\nSELECT 1;\n"))
	if offset != want {
		t.Fatalf("offset=%d, want %d", offset, want)
	}
}

func TestInitialLookbackOffsetTailsWhenNoRecordMatches(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "slow.log")
	content := "# Time: 2026-08-02T09:50:00\n# Query_time: 1.0 Lock_time: 0 Rows_sent: 1 Rows_examined: 1\nSELECT 1;\n"
	if err := os.WriteFile(path, []byte(content), 0600); err != nil {
		t.Fatal(err)
	}
	offset, err := initialLookbackOffset(path, 60, "Asia/Shanghai", time.Date(2026, 8, 2, 10, 3, 0, 0, time.FixedZone("CST", 8*3600)))
	if err != nil {
		t.Fatal(err)
	}
	if offset != int64(len(content)) {
		t.Fatalf("offset=%d, want tail=%d", offset, len(content))
	}
}

func TestInspectFormatReturnsOnlyRequiredBooleanSignature(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "slow.log")
	if err := os.WriteFile(path, []byte("# Time: 2026-08-02T10:00:01\n# Query_time: 1.0 Lock_time: 0 Rows_sent: 1 Rows_examined: 1\nSELECT 1;\n"), 0600); err != nil {
		t.Fatal(err)
	}
	signature := inspectFormat([]fileMeta{{Path: path}})
	if signature["parser_profile"] != "tdsql_mysql_slowlog_v1" || signature["time_header"] != true || signature["query_time_header"] != true {
		t.Fatalf("unexpected format signature: %#v", signature)
	}
}

func TestParseSlowLogTimeAcceptsTDSQLCompactMicrosecondFormat(t *testing.T) {
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := parseSlowLogTime("260731 13:45:06 303896", location)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Format("2006-01-02T15:04:05.000000") != "2026-07-31T13:45:06.303896" {
		t.Fatalf("unexpected parse result: %s", parsed.Format(time.RFC3339Nano))
	}
}

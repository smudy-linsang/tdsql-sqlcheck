//go:build linux

package main

import (
	"fmt"
	"os"
	"syscall"
)

func fileIdentity(info os.FileInfo) (string, error) {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return "", fmt.Errorf("filesystem does not expose stat identity")
	}
	return fmt.Sprintf("dev:%d:ino:%d", stat.Dev, stat.Ino), nil
}

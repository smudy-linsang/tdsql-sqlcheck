//go:build !linux

package main

import (
	"fmt"
	"os"
)

// The exporter is a Linux deployment artifact. Refusing a non-Linux file
// identity is safer than inventing a weak cursor identity during local builds.
func fileIdentity(info os.FileInfo) (string, error) {
	return "", fmt.Errorf("raw_slowlog_exporter requires Linux stat identity")
}

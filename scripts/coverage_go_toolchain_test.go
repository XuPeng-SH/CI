package main

import (
	"strings"
	"testing"
)

func TestCoverageToolchain(t *testing.T) {
	for _, test := range []struct {
		name    string
		content string
		want    string
	}{
		{"language", "go 1.24\n", "go1.24.0"},
		{"patch", "go 1.26.4\n", "go1.26.4"},
		{"preferred", "go 1.24\ntoolchain go1.25.4\n", "go1.25.4"},
		{"older preference", "go 1.25.4\ntoolchain go1.24.9\n", "go1.25.4"},
		{"explicit default", "go 1.25\ntoolchain default\n", "go1.25.0"},
		{"prerelease preference", "go 1.26\ntoolchain go1.26rc2\n", "go1.26rc2"},
		{"future directives", "go 1.25.4\nignore ./old\nnewdirective (\n  go 1.2\n)\n", "go1.25.4"},
		{"missing version", "module example\n", ""},
		{"duplicate version", "go 1.23\ngo 1.24\n", ""},
		{"invalid toolchain", "go 1.24\ntoolchain go1.25.4-../../invalid\n", ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, err := coverageToolchain(strings.NewReader(test.content))
			if got != test.want || (err != nil) != (test.want == "") {
				t.Fatalf("got %q, %v; want %q", got, err, test.want)
			}
		})
	}
}

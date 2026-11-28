// Resolve the canary's Go release without loading the PR module.
package main

import (
	"bufio"
	"fmt"
	"go/version"
	"io"
	"os"
	"strings"
)

func coverageToolchain(input io.Reader) (string, error) {
	var minimum, preferred string
	scanner := bufio.NewScanner(input)
	scanner.Buffer(nil, 512*1024)
	inBlock := false
	for scanner.Scan() {
		line, _, _ := strings.Cut(scanner.Text(), "//")
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		if inBlock {
			inBlock = fields[0] != ")"
			continue
		}
		if fields[len(fields)-1] == "(" {
			inBlock = true
			continue
		}
		switch fields[0] {
		case "go":
			if len(fields) != 2 || minimum != "" {
				return "", fmt.Errorf("invalid go directive")
			}
			minimum = "go" + fields[1]
		case "toolchain":
			if len(fields) != 2 || preferred != "" {
				return "", fmt.Errorf("invalid toolchain directive")
			}
			preferred = fields[1]
		}
	}
	if err := scanner.Err(); err != nil {
		return "", err
	}
	if !version.IsValid(minimum) || !strings.Contains(minimum, ".") || strings.Contains(minimum, "-") {
		return "", fmt.Errorf("go directive must name a Go version")
	}
	if preferred != "" && preferred != "default" {
		if !version.IsValid(preferred) || strings.Contains(preferred, "-") {
			return "", fmt.Errorf("toolchain directive must name a standard Go toolchain")
		}
		if version.Compare(preferred, minimum) > 0 {
			minimum = preferred
		}
	}
	// Starting at Go 1.21, a language version selects the initial patch release.
	if version.Lang(minimum) == minimum && version.Compare(minimum, "go1.21") >= 0 {
		minimum += ".0"
	}
	return minimum, nil
}

func main() {
	toolchain, err := coverageToolchain(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println(toolchain)
}

package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

var version = "0.11.0"

type Globals struct {
	BaseURL string
	Profile string
	APIKey  string
	Timeout float64
	JSON    bool
}

type Resolved struct {
	BaseURL      string
	APIKey       string
	Timeout      float64
	DefaultModel string
}

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	root := newRootCmd()
	root.SetArgs(args)
	executed, err := root.ExecuteC()
	if err != nil {
		var exit commandExit
		if errors.As(err, &exit) {
			return exit.code
		}
		fmt.Fprintln(os.Stderr, "error:", err)
		if executed != nil {
			fmt.Fprintln(os.Stderr)
			fmt.Fprint(os.Stderr, executed.UsageString())
		}
		return 2
	}
	return 0
}

func emitJSON(v any) int {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		return 1
	}
	return 0
}

func fail(err error, asJSON bool, fallback int) int {
	code := fallback
	if e, ok := err.(*APIError); ok {
		code = exitCode(e.Status)
	}
	if asJSON {
		enc := json.NewEncoder(os.Stderr)
		enc.SetEscapeHTML(false)
		_ = enc.Encode(map[string]any{
			"ok": false,
			"error": map[string]any{
				"code": "cli_error", "message": err.Error(), "exit_code": code,
			},
		})
	} else {
		fmt.Fprintln(os.Stderr, "error:", err)
	}
	return code
}

func exitCode(status int) int {
	switch {
	case status == 0:
		return 5
	case status == 2 || status == 400 || status == 422:
		return 2
	case status == 401 || status == 403:
		return 3
	case status == 404:
		return 4
	case status == 409:
		return 6
	case status == 429 || status >= 500:
		return 5
	default:
		return 1
	}
}

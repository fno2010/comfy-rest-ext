package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
)

const version = "0.1.0"

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
	g, rest, err := parseGlobals(args)
	if err != nil {
		return fail(err, g.JSON, 2)
	}
	if len(rest) == 0 || rest[0] == "help" || rest[0] == "-h" || rest[0] == "--help" {
		usage()
		return 0
	}
	cfg, err := loadConfig()
	if err != nil {
		return fail(err, g.JSON, 2)
	}
	cmd := rest[0]
	if cmd == "config" {
		return runConfig(&cfg, g, rest[1:])
	}
	r := resolveProfile(cfg, g)
	client := NewClient(r)
	switch cmd {
	case "health":
		return cmdHealth(client, g.JSON)
	case "capabilities":
		return cmdCapabilities(client, g.JSON, rest[1:])
	case "doctor":
		return cmdDoctor(client, g.JSON)
	case "provision":
		return cmdProvision(client, g.JSON, rest[1:])
	case "restart":
		return cmdRestart(client, g.JSON, rest[1:])
	case "wait-reconnect":
		return cmdWaitReconnect(client, g.JSON, rest[1:])
	case "downloads":
		return cmdDownloads(client, g.JSON, rest[1:])
	case "models":
		return cmdModels(client, g.JSON, rest[1:])
	case "videos":
		return cmdVideos(client, g.JSON, rest[1:], r.DefaultModel)
	case "images":
		return cmdImages(client, g.JSON, rest[1:], r.DefaultModel)
	case "version", "--version":
		if g.JSON {
			return emitJSON(map[string]any{"version": version})
		}
		fmt.Println(version)
		return 0
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", cmd)
		usage()
		return 2
	}
}

func parseGlobals(args []string) (Globals, []string, error) {
	var g Globals
	i := 0
	for i < len(args) {
		a := args[i]
		if !strings.HasPrefix(a, "-") || a == "-" {
			break
		}
		next := func(name string) (string, error) {
			if i+1 >= len(args) {
				return "", fmt.Errorf("%s requires a value", name)
			}
			i++
			return args[i], nil
		}
		var err error
		switch {
		case a == "--json":
			g.JSON = true
		case a == "--base-url":
			g.BaseURL, err = next(a)
		case strings.HasPrefix(a, "--base-url="):
			g.BaseURL = strings.TrimPrefix(a, "--base-url=")
		case a == "--profile":
			g.Profile, err = next(a)
		case strings.HasPrefix(a, "--profile="):
			g.Profile = strings.TrimPrefix(a, "--profile=")
		case a == "--api-key":
			g.APIKey, err = next(a)
		case strings.HasPrefix(a, "--api-key="):
			g.APIKey = strings.TrimPrefix(a, "--api-key=")
		case a == "--timeout":
			var v string
			v, err = next(a)
			if err == nil {
				g.Timeout, err = strconv.ParseFloat(v, 64)
			}
		case strings.HasPrefix(a, "--timeout="):
			g.Timeout, err = strconv.ParseFloat(strings.TrimPrefix(a, "--timeout="), 64)
		case a == "--version":
			return g, []string{"version"}, nil
		default:
			return g, args[i:], nil
		}
		if err != nil {
			return g, nil, err
		}
		i++
	}
	return g, args[i:], nil
}

func usage() {
	fmt.Println(`Usage: comfy-rest-ext [--base-url URL] [--profile NAME] [--api-key KEY] [--timeout SEC] [--json] <command>

Commands:
  health
  capabilities [--model ID] [--capability ID]
  doctor
  provision CAPABILITY... [--yes] [--retry-task ID]
  restart [--yes] [--retry-task ID] [--reason TEXT]
  wait-reconnect [--since-boot ID] [--retry-task ID]
  downloads [list|create URL|status ID|cancel ID]
  models [list|get ID]
  videos [generate PROMPT|list|status ID|wait ID|content ID|retry ID]
  images [generate PROMPT|edit PROMPT|list|status ID|content ID]
  config [list|use NAME|set NAME KEY VALUE|get NAME [KEY]|path]
  version`)
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

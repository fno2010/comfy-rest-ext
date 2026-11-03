package main

import (
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func newFlagSet(name string) *flag.FlagSet {
	fs := flag.NewFlagSet(name, flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	return fs
}

func runConfig(cfg *Config, g Globals, args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: comfy-rest-ext config list|use|set|get|path")
		return 2
	}
	switch args[0] {
	case "path":
		if g.JSON {
			return emitJSON(map[string]any{"path": configPath()})
		}
		fmt.Println(configPath())
		return 0
	case "list":
		if g.JSON {
			return emitJSON(redactedConfig(*cfg))
		}
		fmt.Printf("default profile: %s\n", cfg.DefaultProfile)
		for _, name := range sortedKeys(cfg.Profiles) {
			p := cfg.Profiles[name]
			fmt.Printf("%s", name)
			var bits []string
			if p.BaseURL != "" {
				bits = append(bits, "base_url="+p.BaseURL)
			}
			if p.APIKey != "" {
				bits = append(bits, "api_key=***")
			}
			if p.Timeout != 0 {
				bits = append(bits, fmt.Sprintf("timeout=%g", p.Timeout))
			}
			if p.DefaultModel != "" {
				bits = append(bits, "default_model="+p.DefaultModel)
			}
			if len(bits) > 0 {
				fmt.Printf("  (%s)", strings.Join(bits, ", "))
			}
			fmt.Println()
		}
		if len(cfg.Profiles) == 0 {
			fmt.Println("(no profiles configured)")
		}
		return 0
	case "use":
		if len(args) != 2 {
			return fail(fmt.Errorf("usage: config use PROFILE"), g.JSON, 2)
		}
		if _, ok := cfg.Profiles[args[1]]; !ok {
			return fail(fmt.Errorf("profile not found: %s", args[1]), g.JSON, 4)
		}
		cfg.DefaultProfile = args[1]
		path, err := saveConfig(*cfg)
		if err != nil {
			return fail(err, g.JSON, 2)
		}
		if g.JSON {
			return emitJSON(map[string]any{"default_profile": args[1], "path": path})
		}
		fmt.Printf("default profile: %s\n", args[1])
		return 0
	case "set":
		if len(args) != 4 {
			return fail(fmt.Errorf("usage: config set PROFILE KEY VALUE"), g.JSON, 2)
		}
		profileName, key, value := args[1], args[2], args[3]
		if !validProfileName(profileName) {
			return fail(fmt.Errorf("invalid profile name: %s", profileName), g.JSON, 2)
		}
		if !configFields[key] {
			return fail(fmt.Errorf("unknown config key: %s", key), g.JSON, 2)
		}
		if key == "api_key" && value == "-" {
			data, err := io.ReadAll(os.Stdin)
			if err != nil {
				return fail(err, g.JSON, 1)
			}
			value = strings.TrimRight(string(data), "\r\n")
		}
		p := cfg.Profiles[profileName]
		switch key {
		case "base_url":
			p.BaseURL = value
		case "api_key":
			p.APIKey = value
		case "timeout":
			f, err := strconv.ParseFloat(value, 64)
			if err != nil {
				return fail(fmt.Errorf("timeout must be a number"), g.JSON, 2)
			}
			p.Timeout = f
		case "default_model":
			p.DefaultModel = value
		}
		cfg.Profiles[profileName] = p
		path, err := saveConfig(*cfg)
		if err != nil {
			return fail(err, g.JSON, 2)
		}
		if g.JSON {
			return emitJSON(map[string]any{"profile": profileName, "key": key, "path": path})
		}
		fmt.Printf("set %s for profile %s\n", key, profileName)
		return 0
	case "get":
		if len(args) < 2 || len(args) > 3 {
			return fail(fmt.Errorf("usage: config get PROFILE [KEY]"), g.JSON, 2)
		}
		p, ok := cfg.Profiles[args[1]]
		if !ok {
			return fail(fmt.Errorf("profile not found: %s", args[1]), g.JSON, 4)
		}
		values := profileMap(p)
		if len(args) == 3 {
			v, ok := values[args[2]]
			if !ok {
				return fail(fmt.Errorf("key not set: %s", args[2]), g.JSON, 4)
			}
			if args[2] == "api_key" && v != "" {
				v = "***"
			}
			if g.JSON {
				return emitJSON(map[string]any{"profile": args[1], "key": args[2], "value": v})
			}
			fmt.Println(v)
			return 0
		}
		if g.JSON {
			return emitJSON(values)
		}
		for _, k := range []string{"api_key", "base_url", "default_model", "timeout"} {
			if v, ok := values[k]; ok {
				fmt.Printf("%s=%v\n", k, v)
			}
		}
		return 0
	default:
		return fail(fmt.Errorf("unknown config command: %s", args[0]), g.JSON, 2)
	}
}

func redactedConfig(cfg Config) map[string]any {
	out := map[string]any{"default_profile": cfg.DefaultProfile, "profiles": map[string]any{}}
	for name, p := range cfg.Profiles {
		out["profiles"].(map[string]any)[name] = profileMap(Profile{
			BaseURL: p.BaseURL, APIKey: redact(p.APIKey), Timeout: p.Timeout,
			DefaultModel: p.DefaultModel,
		})
	}
	return out
}

func profileMap(p Profile) map[string]any {
	m := map[string]any{}
	if p.APIKey != "" {
		m["api_key"] = redact(p.APIKey)
	}
	if p.BaseURL != "" {
		m["base_url"] = p.BaseURL
	}
	if p.DefaultModel != "" {
		m["default_model"] = p.DefaultModel
	}
	if p.Timeout != 0 {
		m["timeout"] = p.Timeout
	}
	return m
}

func redact(s string) string {
	if s == "" {
		return ""
	}
	return "***"
}

func cmdHealth(c *Client, asJSON bool) int {
	m, err := c.getJSON("/v2/extension/health", nil)
	if err != nil {
		return fail(err, asJSON, 1)
	}
	if asJSON {
		return emitJSON(m)
	}
	fmt.Println("status:   ", m["status"])
	fmt.Println("extension:", m["extension"])
	if v, ok := m["boot_id"]; ok {
		fmt.Println("boot_id:  ", v)
	}
	return 0
}

func cmdCapabilities(c *Client, asJSON bool, args []string) int {
	fs := newFlagSet("capabilities")
	model := fs.String("model", "", "filter by model id")
	capability := fs.String("capability", "", "filter by capability id")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	q := url.Values{}
	if *model != "" {
		q.Set("model", *model)
	}
	if *capability != "" {
		q.Set("capability", *capability)
	}
	m, err := c.getJSON("/v2/extension/capabilities", q)
	if err != nil {
		return fail(err, asJSON, 1)
	}
	if asJSON {
		return emitJSON(m)
	}
	if summary, ok := m["summary"].(map[string]any); ok {
		for k, v := range summary {
			fmt.Printf("%s=%v\n", k, v)
		}
	}
	if items, ok := m["capabilities"].([]any); ok {
		for _, item := range items {
			c, _ := item.(map[string]any)
			fmt.Printf("%-10v %v\n", c["status"], c["id"])
		}
	}
	return 0
}

func cmdDoctor(c *Client, asJSON bool) int {
	health, err := c.getJSON("/v2/extension/health", nil)
	if err != nil {
		return fail(err, asJSON, 1)
	}
	caps, err := c.getJSON("/v2/extension/capabilities", nil)
	if err != nil {
		return fail(err, asJSON, 1)
	}
	prov, provErr := c.getJSON("/v2/extension/provisioners", nil)
	out := map[string]any{"health": health, "capabilities": caps}
	if provErr == nil {
		out["provisioners"] = prov
	}
	if asJSON {
		return emitJSON(out)
	}
	fmt.Println("health:", health["status"])
	if summary, ok := caps["summary"].(map[string]any); ok {
		for k, v := range summary {
			fmt.Printf("capabilities %s=%v\n", k, v)
		}
	}
	if provErr != nil {
		fmt.Println("provisioners: unavailable")
	}
	return 0
}

func cmdModels(c *Client, asJSON bool, args []string) int {
	action := "list"
	id := ""
	if len(args) > 0 {
		action = args[0]
	}
	if action == "get" {
		if len(args) != 2 {
			return fail(fmt.Errorf("usage: models get ID"), asJSON, 2)
		}
		id = args[1]
	}
	m, err := c.getJSON(func() string {
		if id != "" {
			return "/v1/models/" + url.PathEscape(id)
		}
		return "/v1/models"
	}(), nil)
	if err != nil {
		return fail(err, asJSON, 1)
	}
	if asJSON {
		return emitJSON(m)
	}
	if id != "" {
		fmt.Println(m["id"], m["modality"])
		return 0
	}
	if items, ok := m["data"].([]any); ok {
		for _, item := range items {
			model, _ := item.(map[string]any)
			fmt.Printf("%-28v %v\n", model["id"], model["modality"])
		}
	}
	return 0
}

func cmdVideos(c *Client, asJSON bool, args []string) int {
	if len(args) == 0 {
		args = []string{"list"}
	}
	switch args[0] {
	case "list":
		m, err := c.getJSON("/v1/videos", nil)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		if items, ok := m["data"].([]any); ok {
			for _, item := range items {
				task, _ := item.(map[string]any)
				fmt.Printf("%-42v %v\n", task["id"], task["status"])
			}
		}
		return 0
	case "status":
		if len(args) != 2 {
			return fail(fmt.Errorf("usage: videos status ID"), asJSON, 2)
		}
		m, err := c.getJSON("/v1/videos/"+url.PathEscape(args[1]), nil)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		printStatus(m)
		return 0
	case "wait":
		fs := newFlagSet("videos wait")
		timeout := fs.Float64("timeout", 300, "max seconds to wait; 0 waits indefinitely")
		interval := fs.Float64("interval", 5, "poll interval seconds")
		if err := fs.Parse(args[1:]); err != nil || fs.NArg() != 1 {
			return 2
		}
		id := fs.Arg(0)
		deadline := time.Time{}
		if *timeout > 0 {
			deadline = time.Now().Add(time.Duration(*timeout * float64(time.Second)))
		}
		for {
			m, err := c.getJSON("/v1/videos/"+url.PathEscape(id), nil)
			if err != nil {
				return fail(err, asJSON, 1)
			}
			status := fmt.Sprint(m["status"])
			if asJSON {
				// Keep polling quiet in JSON mode.
			} else {
				fmt.Printf("%s %v%%\n", status, m["progress"])
			}
			if status == "completed" {
				if asJSON {
					return emitJSON(m)
				}
				printStatus(m)
				return 0
			}
			if status == "failed" || status == "cancelled" {
				if asJSON {
					return emitJSON(m)
				}
				printStatus(m)
				return 1
			}
			if !deadline.IsZero() && time.Now().After(deadline) {
				return fail(fmt.Errorf("timed out waiting for %s", id), asJSON, 5)
			}
			if *interval > 0 {
				time.Sleep(time.Duration(*interval * float64(time.Second)))
			}
		}
	case "content":
		fs := newFlagSet("videos content")
		out := fs.String("out", "", "output path")
		if err := fs.Parse(args[1:]); err != nil || fs.NArg() != 1 {
			return 2
		}
		id := fs.Arg(0)
		path := *out
		if path == "" {
			path = id + ".mp4"
		}
		data, err := c.getRaw("/v1/videos/" + url.PathEscape(id) + "/content")
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if err := os.WriteFile(path, data, 0o644); err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(map[string]any{"path": path, "bytes": len(data)})
		}
		fmt.Println("saved:", filepath.Clean(path))
		return 0
	case "retry":
		if len(args) != 2 {
			return fail(fmt.Errorf("usage: videos retry ID"), asJSON, 2)
		}
		m, err := c.postJSON("/v1/videos/"+url.PathEscape(args[1])+"/retry", map[string]any{})
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		printStatus(m)
		return 0
	default:
		return fail(fmt.Errorf("unknown videos command: %s", args[0]), asJSON, 2)
	}
}

func cmdImages(c *Client, asJSON bool, args []string) int {
	if len(args) == 0 {
		args = []string{"list"}
	}
	switch args[0] {
	case "list":
		m, err := c.getJSON("/v2/extension/image", nil)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		if items, ok := m["tasks"].([]any); ok {
			for _, item := range items {
				task, _ := item.(map[string]any)
				fmt.Printf("%-42v %v\n", task["id"], task["status"])
			}
		}
		return 0
	case "status":
		if len(args) != 2 {
			return fail(fmt.Errorf("usage: images status ID"), asJSON, 2)
		}
		m, err := c.getJSON("/v2/extension/image/"+url.PathEscape(args[1]), nil)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		printStatus(m)
		return 0
	case "content":
		fs := newFlagSet("images content")
		out := fs.String("out", "", "output path")
		index := fs.Int("index", 0, "output index")
		if err := fs.Parse(args[1:]); err != nil || fs.NArg() != 1 {
			return 2
		}
		id := fs.Arg(0)
		path := *out
		if path == "" {
			path = fmt.Sprintf("%s_%d.png", id, *index)
		}
		q := url.Values{"index": []string{strconv.Itoa(*index)}}
		data, err := c.getRaw("/v2/extension/image/" + url.PathEscape(id) + "/content?" + q.Encode())
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if err := os.WriteFile(path, data, 0o644); err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(map[string]any{"path": path, "bytes": len(data)})
		}
		fmt.Println("saved:", filepath.Clean(path))
		return 0
	default:
		return fail(fmt.Errorf("unknown images command: %s", args[0]), asJSON, 2)
	}
}

func printStatus(m map[string]any) {
	fmt.Println("id:     ", m["id"])
	fmt.Println("status: ", m["status"])
	if v, ok := m["progress"]; ok {
		fmt.Println("progress:", v)
	}
	if v, ok := m["error"]; ok && v != nil {
		fmt.Println("error:  ", v)
	}
}

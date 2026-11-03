package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"mime/multipart"
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

// parseArgs accepts options before or after positional arguments. The
// stdlib flag package stops at the first positional; agent invocations often
// put the prompt/task id first.
func parseArgs(fs *flag.FlagSet, args []string, valueFlags map[string]bool) ([]string, error) {
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		a := args[i]
		if a == "--" {
			positional = append(positional, args[i+1:]...)
			break
		}
		if strings.HasPrefix(a, "-") && a != "-" {
			flags = append(flags, a)
			if !strings.Contains(a, "=") && valueFlags[a] && i+1 < len(args) {
				i++
				flags = append(flags, args[i])
			}
			continue
		}
		positional = append(positional, a)
	}
	if err := fs.Parse(flags); err != nil {
		return nil, err
	}
	return positional, nil
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

func cmdVideos(c *Client, asJSON bool, args []string, defaultModel string) int {
	if len(args) == 0 {
		args = []string{"list"}
	}
	switch args[0] {
	case "generate":
		return generateVideo(c, asJSON, args[1:], defaultModel)
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
		pos, err := parseArgs(fs, args[1:], map[string]bool{"--timeout": true, "--interval": true})
		if err != nil || len(pos) != 1 {
			return 2
		}
		id := pos[0]
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
		pos, err := parseArgs(fs, args[1:], map[string]bool{"--out": true})
		if err != nil || len(pos) != 1 {
			return 2
		}
		id := pos[0]
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

func cmdImages(c *Client, asJSON bool, args []string, defaultModel string) int {
	if len(args) == 0 {
		args = []string{"list"}
	}
	switch args[0] {
	case "generate":
		return generateImage(c, asJSON, args[1:], defaultModel, false)
	case "edit":
		return generateImage(c, asJSON, args[1:], defaultModel, true)
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
		pos, err := parseArgs(fs, args[1:], map[string]bool{"--out": true, "--index": true})
		if err != nil || len(pos) != 1 {
			return 2
		}
		id := pos[0]
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

type stringList []string

func (s *stringList) String() string     { return strings.Join(*s, ",") }
func (s *stringList) Set(v string) error { *s = append(*s, v); return nil }

func readSpeed(raw string) (any, error) {
	if raw == "" {
		return nil, nil
	}
	if strings.HasPrefix(raw, "@") {
		data, err := os.ReadFile(strings.TrimPrefix(raw, "@"))
		if err != nil {
			return nil, err
		}
		var out any
		if err := json.Unmarshal(data, &out); err != nil {
			return nil, err
		}
		return out, nil
	}
	var out any
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		return nil, fmt.Errorf("invalid --speed-json: %w", err)
	}
	return out, nil
}

func generateVideo(c *Client, asJSON bool, args []string, defaultModel string) int {
	fs := newFlagSet("videos generate")
	model := fs.String("model", "", "video model id")
	width := fs.Int("width", 0, "frame width")
	height := fs.Int("height", 0, "frame height")
	seconds := fs.Float64("seconds", 0, "video length in seconds")
	seed := fs.Int64("seed", 0, "generation seed")
	speedJSON := fs.String("speed-json", "", "speed JSON or @file")
	var images, videos stringList
	fs.Var(&images, "image", "reference image path (repeatable)")
	fs.Var(&videos, "video", "reference video path (repeatable)")
	pos, err := parseArgs(fs, args, map[string]bool{
		"--model": true, "--width": true, "--height": true, "--seconds": true,
		"--seed": true, "--speed-json": true, "--image": true, "--video": true,
	})
	if err != nil || len(pos) < 1 {
		return 2
	}
	prompt := pos[0]
	payload := map[string]any{"prompt": prompt}
	if *model != "" {
		payload["model"] = *model
	} else if defaultModel != "" {
		payload["model"] = defaultModel
	}
	if *width > 0 {
		payload["width"] = *width
	}
	if *height > 0 {
		payload["height"] = *height
	}
	if *seconds > 0 {
		payload["seconds"] = *seconds
	}
	if *seed != 0 {
		payload["seed"] = *seed
	}
	if *speedJSON != "" {
		speed, err := readSpeed(*speedJSON)
		if err != nil {
			return fail(err, asJSON, 2)
		}
		payload["speed"] = speed
	}
	if len(images) > 0 || len(videos) > 0 {
		items := []any{map[string]any{"type": "input_text", "text": prompt}}
		role := "first_frame"
		if len(images) > 1 || len(videos) > 0 || strings.HasSuffix(*model, "-r2v") {
			role = "reference_image"
		}
		for _, path := range images {
			data, err := os.ReadFile(path)
			if err != nil {
				return fail(err, asJSON, 2)
			}
			ext := strings.TrimPrefix(strings.ToLower(filepath.Ext(path)), ".")
			if ext == "" {
				ext = "png"
			}
			items = append(items, map[string]any{
				"type":      "input_image",
				"image_url": "data:image/" + ext + ";base64," + base64.StdEncoding.EncodeToString(data),
				"role":      role,
			})
		}
		for _, path := range videos {
			data, err := os.ReadFile(path)
			if err != nil {
				return fail(err, asJSON, 2)
			}
			ext := strings.TrimPrefix(strings.ToLower(filepath.Ext(path)), ".")
			if ext == "" {
				ext = "mp4"
			}
			items = append(items, map[string]any{
				"type":      "input_video",
				"video_url": "data:video/" + ext + ";base64," + base64.StdEncoding.EncodeToString(data),
			})
		}
		payload["input"] = items
		delete(payload, "prompt")
	}
	m, err := c.postJSON("/v1/videos", payload)
	if err != nil {
		return fail(err, asJSON, 1)
	}
	if asJSON {
		return emitJSON(m)
	}
	fmt.Printf("task %v queued (status: %v)\n", m["id"], m["status"])
	return 0
}

func generateImage(c *Client, asJSON bool, args []string, defaultModel string, edit bool) int {
	fs := newFlagSet("images generate")
	model := fs.String("model", defaultModel, "image model id")
	size := fs.String("size", "", "WIDTHxHEIGHT")
	width := fs.Int("width", 0, "output width")
	height := fs.Int("height", 0, "output height")
	n := fs.Int("n", 0, "number of images")
	seed := fs.Int64("seed", 0, "generation seed")
	out := fs.String("out", "image.png", "output path")
	var images stringList
	fs.Var(&images, "image", "reference image path (repeatable)")
	pos, err := parseArgs(fs, args, map[string]bool{
		"--model": true, "--size": true, "--width": true, "--height": true,
		"--n": true, "--seed": true, "--out": true, "--image": true,
	})
	if err != nil || len(pos) < 1 {
		return 2
	}
	prompt := pos[0]
	payload := map[string]any{"prompt": prompt}
	if *model != "" {
		payload["model"] = *model
	}
	if *size != "" {
		payload["size"] = *size
	}
	if *width > 0 {
		payload["width"] = *width
	}
	if *height > 0 {
		payload["height"] = *height
	}
	if *n > 0 {
		payload["n"] = *n
	}
	if *seed != 0 {
		payload["seed"] = *seed
	}
	var data []byte
	var reqErr error
	if edit {
		if len(images) == 0 {
			return fail(fmt.Errorf("images edit requires --image"), asJSON, 2)
		}
		var body bytes.Buffer
		writer := multipart.NewWriter(&body)
		for key, value := range payload {
			_ = writer.WriteField(key, fmt.Sprint(value))
		}
		for i, path := range images {
			file, e := os.Open(path)
			if e != nil {
				return fail(e, asJSON, 2)
			}
			part, e := writer.CreateFormFile("image", fmt.Sprintf("image%d%s", i+1, filepath.Ext(path)))
			if e != nil {
				file.Close()
				return fail(e, asJSON, 1)
			}
			_, e = io.Copy(part, file)
			file.Close()
			if e != nil {
				return fail(e, asJSON, 1)
			}
		}
		if err := writer.Close(); err != nil {
			return fail(err, asJSON, 1)
		}
		data, _, reqErr = c.request("POST", "/v1/images/edits", body.Bytes(), writer.FormDataContentType(), false)
	} else {
		data, _, reqErr = c.request("POST", "/v1/images/generations", mustJSON(payload), "application/json", false)
	}
	if reqErr != nil {
		return fail(reqErr, asJSON, 1)
	}
	var response map[string]any
	if err := json.Unmarshal(data, &response); err != nil {
		return fail(err, asJSON, 1)
	}
	if asJSON {
		return emitJSON(response)
	}
	items, _ := response["data"].([]any)
	for i, item := range items {
		obj, _ := item.(map[string]any)
		if b64, ok := obj["b64_json"].(string); ok {
			raw, err := base64.StdEncoding.DecodeString(b64)
			if err != nil {
				return fail(err, asJSON, 1)
			}
			path := *out
			if i > 0 {
				ext := filepath.Ext(path)
				if ext == "" {
					ext = ".png"
				}
				path = strings.TrimSuffix(path, ext) + "_" + strconv.Itoa(i+1) + ext
			}
			if err := os.WriteFile(path, raw, 0o644); err != nil {
				return fail(err, asJSON, 1)
			}
			fmt.Println("saved:", path)
		}
	}
	return 0
}

func mustJSON(v any) []byte {
	data, _ := json.Marshal(v)
	return data
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

func retryVideo(c *Client, id string) (map[string]any, error) {
	return c.postJSON("/v1/videos/"+url.PathEscape(id)+"/retry", map[string]any{})
}

func waitHealth(c *Client, sinceBoot string, timeout, interval float64) (map[string]any, error) {
	start := time.Now()
	for {
		m, err := c.getJSON("/v2/extension/health", nil)
		if err == nil {
			if sinceBoot == "" || fmt.Sprint(m["boot_id"]) != sinceBoot {
				return m, nil
			}
		}
		if timeout > 0 && time.Since(start) >= time.Duration(timeout*float64(time.Second)) {
			return nil, fmt.Errorf("server did not reconnect before timeout")
		}
		if interval > 0 {
			time.Sleep(time.Duration(interval * float64(time.Second)))
		}
	}
}

func cmdProvision(c *Client, asJSON bool, args []string) int {
	fs := newFlagSet("provision")
	yes := fs.Bool("yes", false, "explicitly authorize installation")
	retry := fs.String("retry-task", "", "resume blocked video task")
	pos, err := parseArgs(fs, args, map[string]bool{"--retry-task": true})
	if err != nil || len(pos) == 0 {
		return 2
	}
	payload := map[string]any{"capabilities": pos, "authorized": *yes}
	if *retry != "" {
		payload["retry_task_id"] = *retry
	}
	m, err := c.postJSON("/v2/extension/provision", payload)
	if err != nil {
		return fail(err, asJSON, 1)
	}
	if asJSON {
		return emitJSON(m)
	}
	fmt.Println("status:", m["status"])
	if actions, ok := m["actions"].([]any); ok {
		for _, action := range actions {
			fmt.Println("installed:", action)
		}
	}
	if v, ok := m["restart_required"]; ok && v == true {
		fmt.Println("restart required")
	}
	return 0
}

func cmdRestart(c *Client, asJSON bool, args []string) int {
	fs := newFlagSet("restart")
	yes := fs.Bool("yes", false, "explicitly authorize restart")
	retry := fs.String("retry-task", "", "blocked video task to retry")
	reason := fs.String("reason", "", "restart reason")
	timeout := fs.Float64("timeout", 300, "reconnect timeout")
	interval := fs.Float64("interval", 5, "poll interval")
	if _, err := parseArgs(fs, args, map[string]bool{
		"--retry-task": true, "--reason": true, "--timeout": true, "--interval": true,
	}); err != nil {
		return 2
	}
	payload := map[string]any{"authorized": *yes, "confirm": "restart"}
	if *retry != "" {
		payload["retry_task_id"] = *retry
	}
	if *reason != "" {
		payload["reason"] = *reason
	}
	m, err := c.postJSON("/v2/extension/restart", payload)
	if err != nil {
		return fail(err, asJSON, 1)
	}
	oldBoot := fmt.Sprint(m["boot_id"])
	if oldBoot == "" || oldBoot == "<nil>" {
		return fail(fmt.Errorf("restart response did not include a boot_id"), asJSON, 1)
	}
	if _, err := waitHealth(c, oldBoot, *timeout, *interval); err != nil {
		return fail(err, asJSON, 5)
	}
	if *retry != "" {
		task, err := retryVideo(c, *retry)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(task)
		}
		printStatus(task)
		return 0
	}
	if asJSON {
		return emitJSON(map[string]any{"status": "restarted", "backend": m["backend"]})
	}
	fmt.Println("restarted via", m["backend"])
	return 0
}

func cmdWaitReconnect(c *Client, asJSON bool, args []string) int {
	fs := newFlagSet("wait-reconnect")
	retry := fs.String("retry-task", "", "blocked video task to retry")
	since := fs.String("since-boot", "", "require a different boot_id")
	timeout := fs.Float64("timeout", 300, "reconnect timeout")
	interval := fs.Float64("interval", 5, "poll interval")
	if _, err := parseArgs(fs, args, map[string]bool{
		"--retry-task": true, "--since-boot": true, "--timeout": true, "--interval": true,
	}); err != nil {
		return 2
	}
	if _, err := waitHealth(c, *since, *timeout, *interval); err != nil {
		return fail(err, asJSON, 5)
	}
	if *retry != "" {
		task, err := retryVideo(c, *retry)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(task)
		}
		printStatus(task)
		return 0
	}
	if asJSON {
		return emitJSON(map[string]any{"status": "connected"})
	}
	fmt.Println("connected")
	return 0
}

func cmdDownloads(c *Client, asJSON bool, args []string) int {
	if len(args) == 0 {
		args = []string{"list"}
	}
	switch args[0] {
	case "list":
		fs := newFlagSet("downloads list")
		status := fs.String("status", "all", "status filter")
		if _, err := parseArgs(fs, args[1:], map[string]bool{"--status": true}); err != nil {
			return 2
		}
		q := url.Values{"status": []string{*status}}
		m, err := c.getJSON("/v2/extension/model/download", q)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		if tasks, ok := m["tasks"].([]any); ok {
			for _, item := range tasks {
				task, _ := item.(map[string]any)
				fmt.Printf("%-40v %-12v %v\n", task["task_id"], task["status"], task["url"])
			}
		}
		return 0
	case "create":
		fs := newFlagSet("downloads create")
		folder := fs.String("folder", "", "target folder")
		filename := fs.String("filename", "", "output filename")
		pos, err := parseArgs(fs, args[1:], map[string]bool{"--folder": true, "--filename": true})
		if err != nil || len(pos) != 1 {
			return 2
		}
		payload := map[string]any{"url": pos[0]}
		if *folder != "" {
			payload["folder"] = *folder
		}
		if *filename != "" {
			payload["filename"] = *filename
		}
		m, err := c.postJSON("/v2/extension/model/download", payload)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		fmt.Printf("download task %v queued\n", m["task_id"])
		return 0
	case "status":
		if len(args) != 2 {
			return fail(fmt.Errorf("usage: downloads status ID"), asJSON, 2)
		}
		m, err := c.getJSON("/v2/extension/model/download/"+url.PathEscape(args[1]), nil)
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		for _, k := range []string{"task_id", "status", "progress", "url", "local_path", "error"} {
			if v, ok := m[k]; ok && v != nil {
				fmt.Printf("%-12s %v\n", k, v)
			}
		}
		return 0
	case "cancel":
		if len(args) != 2 {
			return fail(fmt.Errorf("usage: downloads cancel ID"), asJSON, 2)
		}
		m, err := c.deleteJSON("/v2/extension/model/download/" + url.PathEscape(args[1]))
		if err != nil {
			return fail(err, asJSON, 1)
		}
		if asJSON {
			return emitJSON(m)
		}
		fmt.Println("cancelled:", m["task_id"])
		return 0
	default:
		return fail(fmt.Errorf("unknown downloads command: %s", args[0]), asJSON, 2)
	}
}

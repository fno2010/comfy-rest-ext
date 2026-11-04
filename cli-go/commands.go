package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"strings"
	"time"
)

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

func readWorkflow(path string) (map[string]any, error) {
	var data []byte
	var err error
	if path == "-" {
		data, err = io.ReadAll(os.Stdin)
	} else {
		data, err = os.ReadFile(path)
	}
	if err != nil {
		return nil, err
	}
	var obj map[string]any
	if err := json.Unmarshal(data, &obj); err != nil {
		return nil, err
	}
	if prompt, ok := obj["prompt"].(map[string]any); ok {
		return prompt, nil
	}
	return obj, nil
}

func anySlice(v any) []any {
	out, _ := v.([]any)
	return out
}

func escapePath(path string) string {
	parts := strings.Split(path, "/")
	for i := range parts {
		parts[i] = url.PathEscape(parts[i])
	}
	return strings.Join(parts, "/")
}

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

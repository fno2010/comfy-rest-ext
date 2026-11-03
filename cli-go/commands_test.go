package main

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestGenerateVideoArgsAfterPrompt(t *testing.T) {
	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "v1", "status": "queued"})
	}))
	defer srv.Close()
	c := NewClient(Resolved{BaseURL: srv.URL, Timeout: 5})
	if rc := generateVideo(c, true, []string{
		"prompt text", "--model", "minimax-h3", "--width", "512",
		"--height", "512", "--seconds", "1", "--seed", "101",
	}, ""); rc != 0 {
		t.Fatalf("rc=%d", rc)
	}
	if payload["prompt"] != "prompt text" || payload["width"] != float64(512) ||
		payload["height"] != float64(512) || payload["seconds"] != float64(1) {
		t.Fatalf("payload=%#v", payload)
	}
}

func TestGenerateImagePayload(t *testing.T) {
	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"data": []any{}})
	}))
	defer srv.Close()
	c := NewClient(Resolved{BaseURL: srv.URL, Timeout: 5})
	if rc := generateImage(c, true, []string{
		"a cat", "--model", "anima-base", "--size", "512x512", "--n", "2",
	}, "anima-base", false); rc != 0 {
		t.Fatalf("rc=%d", rc)
	}
	if payload["model"] != "anima-base" || payload["size"] != "512x512" || payload["n"] != float64(2) {
		t.Fatalf("payload=%#v", payload)
	}
}

func TestGenerateImageWritesFileInJSONMode(t *testing.T) {
	payload := base64.StdEncoding.EncodeToString([]byte("PNGDATA"))
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"data":[{"b64_json":"` + payload + `"}]}`))
	}))
	defer srv.Close()
	out := filepath.Join(t.TempDir(), "image.png")
	c := NewClient(Resolved{BaseURL: srv.URL, Timeout: 5})
	if rc := generateImage(c, true, []string{
		"a cat", "--model", "anima-base", "--out", out,
	}, "anima-base", false); rc != 0 {
		t.Fatalf("rc=%d", rc)
	}
	data, err := os.ReadFile(out)
	if err != nil || string(data) != "PNGDATA" {
		t.Fatalf("file=%q err=%v", data, err)
	}
}

package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

func executeCLI(t *testing.T, args ...string) int {
	t.Helper()
	isolateConfig(t)
	root := newRootCmd()
	root.SetOut(&bytes.Buffer{})
	root.SetErr(&bytes.Buffer{})
	root.SetArgs(args)
	err := root.Execute()
	if err == nil {
		return 0
	}
	var exit commandExit
	if errors.As(err, &exit) {
		return exit.code
	}
	t.Fatalf("execute %v: %v", args, err)
	return 2
}

func TestVideoGenerateFlags(t *testing.T) {
	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "v1", "status": "queued"})
	}))
	defer srv.Close()
	if rc := executeCLI(t,
		"--base-url", srv.URL, "--json",
		"videos", "generate", "prompt text",
		"--model", "minimax-h3", "--width", "512", "--height", "512",
		"--seconds", "1", "--seed", "101", "--format", "webm", "--codec", "av1",
	); rc != 0 {
		t.Fatalf("rc=%d", rc)
	}
	if payload["prompt"] != "prompt text" || payload["width"] != float64(512) ||
		payload["height"] != float64(512) || payload["seconds"] != float64(1) ||
		payload["format"] != "webm" || payload["codec"] != "av1" {
		t.Fatalf("payload=%#v", payload)
	}
}

func TestImageGeneratePayload(t *testing.T) {
	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"data": []any{}})
	}))
	defer srv.Close()
	if rc := executeCLI(t,
		"--base-url", srv.URL, "--json",
		"images", "generate", "a cat", "--model", "anima-base",
		"--size", "512x512", "--n", "2",
	); rc != 0 {
		t.Fatalf("rc=%d", rc)
	}
	if payload["model"] != "anima-base" || payload["size"] != "512x512" || payload["n"] != float64(2) {
		t.Fatalf("payload=%#v", payload)
	}
	if _, ok := payload["image"]; ok {
		t.Fatalf("images generate must not send image: %#v", payload)
	}
}

func TestImageWritesFileInJSONMode(t *testing.T) {
	payload := base64.StdEncoding.EncodeToString([]byte("PNGDATA"))
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"data":[{"b64_json":"` + payload + `"}]}`))
	}))
	defer srv.Close()
	out := filepath.Join(t.TempDir(), "image.png")
	if rc := executeCLI(t,
		"--base-url", srv.URL, "--json",
		"images", "generate", "a cat", "--model", "anima-base", "--out", out,
	); rc != 0 {
		t.Fatalf("rc=%d", rc)
	}
	data, err := os.ReadFile(out)
	if err != nil || string(data) != "PNGDATA" {
		t.Fatalf("file=%q err=%v", data, err)
	}
}

func TestVideoContentDerivesExtensionFromMediaType(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/videos/v1":
			_, _ = w.Write([]byte(`{"id":"v1","media_type":"video/webm"}`))
		case "/v1/videos/v1/content":
			_, _ = w.Write([]byte("webm-bytes"))
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()
	dir := t.TempDir()
	old, _ := os.Getwd()
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	defer os.Chdir(old)
	if rc := executeCLI(t, "--base-url", srv.URL, "videos", "content", "v1"); rc != 0 {
		t.Fatalf("rc=%d", rc)
	}
	if data, err := os.ReadFile(filepath.Join(dir, "v1.webm")); err != nil || string(data) != "webm-bytes" {
		t.Fatalf("data=%q err=%v", data, err)
	}
}

func walkCommands(cmd *cobra.Command, fn func(*cobra.Command)) {
	fn(cmd)
	for _, child := range cmd.Commands() {
		if child.Name() == "help" {
			continue
		}
		walkCommands(child, fn)
	}
}

func TestEveryCommandHasHelp(t *testing.T) {
	root := newRootCmd()
	walkCommands(root, func(cmd *cobra.Command) {
		if cmd.Short == "" {
			t.Errorf("%s: empty Short", cmd.CommandPath())
		}
		if cmd.RunE == nil && cmd.Run == nil && len(cmd.Commands()) == 0 {
			t.Errorf("%s: no runner and no subcommands", cmd.CommandPath())
		}
		args := append(strings.Fields(strings.TrimPrefix(cmd.CommandPath(), "comfy-rest-ext")), "--help")
		helpRoot := newRootCmd()
		var out, errOut bytes.Buffer
		helpRoot.SetOut(&out)
		helpRoot.SetErr(&errOut)
		helpRoot.SetArgs(args)
		if err := helpRoot.Execute(); err != nil {
			t.Errorf("%s help: %v", cmd.CommandPath(), err)
			return
		}
		text := out.String() + errOut.String()
		if !strings.Contains(text, "Usage:") {
			t.Errorf("%s help missing usage:\n%s", cmd.CommandPath(), text)
		}
		if cmd.LocalFlags().HasFlags() && !strings.Contains(text, "Flags:") {
			t.Errorf("%s help missing flags:\n%s", cmd.CommandPath(), text)
		}
		if len(cmd.Commands()) > 0 && !strings.Contains(text, "Available Commands:") {
			t.Errorf("%s help missing subcommands:\n%s", cmd.CommandPath(), text)
		}
	})
}

func TestOptionalFlagsDocumentOmittedBehavior(t *testing.T) {
	root := newRootCmd()
	walkCommands(root, func(cmd *cobra.Command) {
		cmd.Flags().VisitAll(func(f *pflag.Flag) {
			if f.Name == "help" || f.Name == "version" || f.Hidden {
				return
			}
			if strings.TrimSpace(f.Usage) == "" {
				t.Errorf("%s --%s: empty usage", cmd.CommandPath(), f.Name)
			}
			if f.Annotations[cobra.BashCompOneRequiredFlag] != nil {
				return
			}
			switch f.DefValue {
			case "", "0", "false":
				if !strings.Contains(f.Usage, "omitted:") {
					t.Errorf("%s --%s: no omitted behavior in %q", cmd.CommandPath(), f.Name, f.Usage)
				}
			}
		})
	})
}

func helpText(t *testing.T, args ...string) string {
	t.Helper()
	root := newRootCmd()
	var out, errOut bytes.Buffer
	root.SetOut(&out)
	root.SetErr(&errOut)
	root.SetArgs(append(args, "--help"))
	if err := root.Execute(); err != nil {
		t.Fatalf("help %v: %v", args, err)
	}
	return out.String() + errOut.String()
}

func TestHelpShowsFlagDefaultsAndOmissions(t *testing.T) {
	cases := []struct {
		name string
		args []string
		want []string
	}{
		{
			name: "image generate",
			args: []string{"images", "generate"},
			want: []string{"--out", `default "image.png"`, "does not transcode", "--width", "omitted:"},
		},
		{
			name: "video generate",
			args: []string{"videos", "generate"},
			want: []string{"--format", `default "auto"`, "--codec", "--width", "omitted:", "--height", "omitted:"},
		},
		{
			name: "video wait",
			args: []string{"videos", "wait"},
			want: []string{"--timeout", "default 300", "--interval", "default 5"},
		},
		{
			name: "deps install",
			args: []string{"deps", "install"},
			want: []string{"--workflow", "required", "--sync", "omitted:"},
		},
		{
			name: "pr-cache clear",
			args: []string{"pr-cache", "clear"},
			want: []string{"--confirm", "required"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			text := helpText(t, tc.args...)
			for _, want := range tc.want {
				if !strings.Contains(text, want) {
					t.Errorf("%s help missing %q:\n%s", tc.name, want, text)
				}
			}
		})
	}
}

func TestImageGenerateDoesNotAdvertiseUnusedImageFlag(t *testing.T) {
	text := helpText(t, "images", "generate")
	if strings.Contains(text, "--image") {
		t.Fatalf("images generate advertises --image but does not send it:\n%s", text)
	}
}

func TestPRCacheClearRequiresTrueConfirm(t *testing.T) {
	if rc := executeCLI(t, "pr-cache", "clear", "--confirm=false"); rc != 2 {
		t.Fatalf("rc=%d, want 2", rc)
	}
}

func TestVersionMatchesRepositoryManifest(t *testing.T) {
	versionFile, err := os.ReadFile("../VERSION")
	if err != nil {
		t.Fatal(err)
	}
	skillVersion, err := os.ReadFile("../skills/comfy-rest-ext/cli-version")
	if err != nil {
		t.Fatal(err)
	}
	want := strings.TrimSpace(string(versionFile))
	if version != want {
		t.Fatalf("cli-go version = %q, VERSION = %q", version, want)
	}
	if got := strings.TrimSpace(string(skillVersion)); got != want {
		t.Fatalf("skill cli-version = %q, VERSION = %q", got, want)
	}
}

package main

import (
	"os"
	"path/filepath"
	"testing"
)

func isolateConfig(t *testing.T) {
	t.Helper()
	root := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", root)
	t.Setenv("COMFY_REST_EXT_CONFIG", "")
	for _, k := range []string{
		"COMFY_REST_EXT_PROFILE", "COMFY_REST_EXT_BASE_URL",
		"COMFY_REST_EXT_API_KEY", "COMFY_REST_EXT_TIMEOUT",
		"COMFY_REST_EXT_DEFAULT_MODEL",
	} {
		t.Setenv(k, "")
	}
}

func TestConfigRoundTrip(t *testing.T) {
	isolateConfig(t)
	cfg := Config{
		DefaultProfile: "dev",
		Profiles: map[string]Profile{
			"dev": {BaseURL: "http://example:8188", APIKey: "secret", Timeout: 12.5},
		},
	}
	path, err := saveConfig(cfg)
	if err != nil {
		t.Fatal(err)
	}
	stat, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if stat.Mode().Perm() != 0o600 {
		t.Fatalf("mode = %o, want 600", stat.Mode().Perm())
	}
	loaded, err := loadConfig()
	if err != nil {
		t.Fatal(err)
	}
	if loaded.DefaultProfile != "dev" || loaded.Profiles["dev"].APIKey != "secret" {
		t.Fatalf("loaded %#v", loaded)
	}
}

func TestResolvePrecedence(t *testing.T) {
	isolateConfig(t)
	t.Setenv("COMFY_REST_EXT_BASE_URL", "http://env")
	t.Setenv("COMFY_REST_EXT_API_KEY", "env-key")
	cfg := Config{DefaultProfile: "dev", Profiles: map[string]Profile{
		"dev": {BaseURL: "http://profile", APIKey: "profile-key", Timeout: 10},
	}}
	got := resolveProfile(cfg, Globals{BaseURL: "http://flag", Timeout: 3})
	if got.BaseURL != "http://flag" || got.APIKey != "env-key" || got.Timeout != 3 {
		t.Fatalf("resolved %#v", got)
	}
}

func TestConfigPathUsesXDG(t *testing.T) {
	isolateConfig(t)
	want := filepath.Join(os.Getenv("XDG_CONFIG_HOME"), "comfy-rest-ext", "config.toml")
	if got := configPath(); got != want {
		t.Fatalf("path = %q, want %q", got, want)
	}
}

package main

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type Profile struct {
	BaseURL      string
	APIKey       string
	Timeout      float64
	DefaultModel string
}

type Config struct {
	DefaultProfile string
	Profiles       map[string]Profile
}

var configFields = map[string]bool{
	"base_url": true, "api_key": true, "timeout": true, "default_model": true,
}

func configPath() string {
	if p := os.Getenv("COMFY_REST_EXT_CONFIG"); p != "" {
		return p
	}
	root := os.Getenv("XDG_CONFIG_HOME")
	if root == "" {
		home, _ := os.UserHomeDir()
		root = filepath.Join(home, ".config")
	}
	return filepath.Join(root, "comfy-rest-ext", "config.toml")
}

func parseTOMLValue(raw string) (any, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, fmt.Errorf("empty TOML value")
	}
	if strings.HasPrefix(raw, "\"") {
		v, err := strconv.Unquote(raw)
		if err != nil {
			return nil, fmt.Errorf("invalid quoted value: %w", err)
		}
		return v, nil
	}
	if strings.HasPrefix(raw, "'") {
		if len(raw) < 2 || !strings.HasSuffix(raw, "'") {
			return nil, fmt.Errorf("unterminated TOML string")
		}
		return raw[1 : len(raw)-1], nil
	}
	if raw == "true" {
		return true, nil
	}
	if raw == "false" {
		return false, nil
	}
	if i, err := strconv.Atoi(raw); err == nil {
		return float64(i), nil
	}
	if f, err := strconv.ParseFloat(raw, 64); err == nil {
		return f, nil
	}
	return nil, fmt.Errorf("unsupported TOML value: %q", raw)
}

func loadConfig() (Config, error) {
	cfg := Config{DefaultProfile: "default", Profiles: map[string]Profile{}}
	path := configPath()
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return cfg, nil
	}
	if err != nil {
		return cfg, fmt.Errorf("cannot read config %s: %w", path, err)
	}
	var current string
	scanner := bufio.NewScanner(strings.NewReader(string(data)))
	for n := 1; scanner.Scan(); n++ {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") {
			if !strings.HasSuffix(line, "]") {
				return cfg, fmt.Errorf("invalid section on line %d", n)
			}
			section := strings.TrimSpace(line[1 : len(line)-1])
			parts := strings.Split(section, ".")
			if len(parts) != 2 || parts[0] != "profiles" || !validProfileName(parts[1]) {
				return cfg, fmt.Errorf("invalid section %q", section)
			}
			current = parts[1]
			if _, ok := cfg.Profiles[current]; !ok {
				cfg.Profiles[current] = Profile{}
			}
			continue
		}
		idx := strings.Index(line, "=")
		if idx < 0 {
			return cfg, fmt.Errorf("invalid assignment on line %d", n)
		}
		key := strings.TrimSpace(line[:idx])
		value, err := parseTOMLValue(line[idx+1:])
		if err != nil {
			return cfg, fmt.Errorf("line %d: %w", n, err)
		}
		if current == "" {
			if key != "default_profile" {
				return cfg, fmt.Errorf("unknown top-level key %q", key)
			}
			s, ok := value.(string)
			if !ok || !validProfileName(s) {
				return cfg, fmt.Errorf("invalid default_profile")
			}
			cfg.DefaultProfile = s
			continue
		}
		if !configFields[key] {
			return cfg, fmt.Errorf("unknown profile key %q", key)
		}
		p := cfg.Profiles[current]
		switch key {
		case "base_url":
			v, ok := value.(string)
			if !ok {
				return cfg, fmt.Errorf("base_url must be a string")
			}
			p.BaseURL = v
		case "api_key":
			v, ok := value.(string)
			if !ok {
				return cfg, fmt.Errorf("api_key must be a string")
			}
			p.APIKey = v
		case "timeout":
			f, ok := value.(float64)
			if !ok {
				return cfg, fmt.Errorf("timeout must be a number")
			}
			p.Timeout = f
		case "default_model":
			v, ok := value.(string)
			if !ok {
				return cfg, fmt.Errorf("default_model must be a string")
			}
			p.DefaultModel = v
		}
		cfg.Profiles[current] = p
	}
	if err := scanner.Err(); err != nil {
		return cfg, err
	}
	return cfg, nil
}

func validProfileName(s string) bool {
	if s == "" || len(s) > 80 {
		return false
	}
	for _, r := range s {
		if !(r == '_' || r == '-' || r == '.' ||
			(r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') ||
			(r >= '0' && r <= '9')) {
			return false
		}
	}
	return true
}

func quoteTOML(s string) string {
	return strconv.Quote(s)
}

func saveConfig(cfg Config) (string, error) {
	path := configPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return "", err
	}
	var b strings.Builder
	fmt.Fprintf(&b, "default_profile = %s\n\n", quoteTOML(cfg.DefaultProfile))
	for _, name := range sortedKeys(cfg.Profiles) {
		if !validProfileName(name) {
			return "", fmt.Errorf("invalid profile name: %s", name)
		}
		p := cfg.Profiles[name]
		fmt.Fprintf(&b, "[profiles.%s]\n", name)
		if p.APIKey != "" {
			fmt.Fprintf(&b, "api_key = %s\n", quoteTOML(p.APIKey))
		}
		if p.BaseURL != "" {
			fmt.Fprintf(&b, "base_url = %s\n", quoteTOML(p.BaseURL))
		}
		if p.DefaultModel != "" {
			fmt.Fprintf(&b, "default_model = %s\n", quoteTOML(p.DefaultModel))
		}
		if p.Timeout != 0 {
			fmt.Fprintf(&b, "timeout = %v\n", p.Timeout)
		}
		b.WriteString("\n")
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".config.*.toml")
	if err != nil {
		return "", err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return "", err
	}
	if _, err := tmp.WriteString(b.String()); err != nil {
		tmp.Close()
		return "", err
	}
	if err := tmp.Close(); err != nil {
		return "", err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return "", err
	}
	return path, nil
}

func selectedProfile(cfg Config) string {
	if p := os.Getenv("COMFY_REST_EXT_PROFILE"); p != "" {
		return p
	}
	if cfg.DefaultProfile == "" {
		return "default"
	}
	return cfg.DefaultProfile
}

func resolveProfile(cfg Config, globals Globals) Resolved {
	name := globals.Profile
	if name == "" {
		name = selectedProfile(cfg)
	}
	p := cfg.Profiles[name]
	r := Resolved{
		BaseURL: "http://127.0.0.1:8188",
		Timeout: 60,
	}
	if p.BaseURL != "" {
		r.BaseURL = p.BaseURL
	}
	if p.APIKey != "" {
		r.APIKey = p.APIKey
	}
	if p.Timeout > 0 {
		r.Timeout = p.Timeout
	}
	if p.DefaultModel != "" {
		r.DefaultModel = p.DefaultModel
	}
	if v := os.Getenv("COMFY_REST_EXT_BASE_URL"); v != "" {
		r.BaseURL = v
	}
	if v := os.Getenv("COMFY_REST_EXT_API_KEY"); v != "" {
		r.APIKey = v
	}
	if v := os.Getenv("COMFY_REST_EXT_TIMEOUT"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			r.Timeout = f
		}
	}
	if v := os.Getenv("COMFY_REST_EXT_DEFAULT_MODEL"); v != "" {
		r.DefaultModel = v
	}
	if globals.BaseURL != "" {
		r.BaseURL = globals.BaseURL
	}
	if globals.APIKey != "" {
		r.APIKey = globals.APIKey
	}
	if globals.Timeout > 0 {
		r.Timeout = globals.Timeout
	}
	return r
}

func sortedKeys(m map[string]Profile) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j] < keys[j-1]; j-- {
			keys[j], keys[j-1] = keys[j-1], keys[j]
		}
	}
	return keys
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
	if len(s) <= 8 {
		return "***"
	}
	return s[:4] + "***" + s[len(s)-4:]
}

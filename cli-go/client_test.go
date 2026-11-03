package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestClientAuthAndError(t *testing.T) {
	var seen string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = r.Header.Get("Authorization")
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"error":"blocked"}`))
	}))
	defer srv.Close()
	c := NewClient(Resolved{BaseURL: srv.URL, APIKey: "token", Timeout: 5})
	_, err := c.getJSON("/x", nil)
	if seen != "Bearer token" {
		t.Fatalf("auth = %q", seen)
	}
	apiErr, ok := err.(*APIError)
	if !ok || apiErr.Status != 409 || apiErr.Message != "blocked" {
		t.Fatalf("error = %#v", err)
	}
	if exitCode(apiErr.Status) != 6 {
		t.Fatalf("exit = %d", exitCode(apiErr.Status))
	}
}

func TestClientHealth(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"status":"ok","extension":"comfy-rest-ext"}`))
	}))
	defer srv.Close()
	c := NewClient(Resolved{BaseURL: srv.URL, Timeout: 5})
	m, err := c.getJSON("/v2/extension/health", nil)
	if err != nil || m["status"] != "ok" {
		t.Fatalf("m=%#v err=%v", m, err)
	}
}

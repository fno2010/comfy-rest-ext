package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type APIError struct {
	Status  int
	Message string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("HTTP %d: %s", e.Status, e.Message)
}

type Client struct {
	BaseURL string
	APIKey  string
	HTTP    *http.Client
}

func NewClient(r Resolved) *Client {
	return &Client{
		BaseURL: strings.TrimRight(r.BaseURL, "/"),
		APIKey:  r.APIKey,
		HTTP:    &http.Client{Timeout: time.Duration(r.Timeout * float64(time.Second))},
	}
}

func (c *Client) request(method, path string, body []byte, contentType string, raw bool) ([]byte, int, error) {
	req, err := http.NewRequest(method, c.BaseURL+path, bytes.NewReader(body))
	if err != nil {
		return nil, 0, err
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if c.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.APIKey)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, 0, &APIError{Status: 0, Message: err.Error()}
	}
	defer resp.Body.Close()
	data, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		return nil, resp.StatusCode, readErr
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, resp.StatusCode, parseAPIError(resp.StatusCode, data)
	}
	return data, resp.StatusCode, nil
}

func parseAPIError(status int, data []byte) error {
	var obj map[string]any
	if json.Unmarshal(data, &obj) == nil {
		if e, ok := obj["error"]; ok {
			switch v := e.(type) {
			case string:
				return &APIError{Status: status, Message: v}
			case map[string]any:
				if m, ok := v["message"].(string); ok {
					return &APIError{Status: status, Message: m}
				}
			}
		}
	}
	msg := strings.TrimSpace(string(data))
	if msg == "" {
		msg = http.StatusText(status)
	}
	return &APIError{Status: status, Message: msg}
}

func (c *Client) getJSON(path string, query url.Values) (map[string]any, error) {
	if len(query) > 0 {
		path += "?" + query.Encode()
	}
	data, _, err := c.request(http.MethodGet, path, nil, "", false)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, fmt.Errorf("invalid JSON response: %w", err)
	}
	return out, nil
}

func (c *Client) postJSON(path string, payload any) (map[string]any, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	data, _, err := c.request(http.MethodPost, path, body, "application/json", false)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, fmt.Errorf("invalid JSON response: %w", err)
	}
	return out, nil
}

func (c *Client) getRaw(path string) ([]byte, error) {
	data, _, err := c.request(http.MethodGet, path, nil, "", true)
	return data, err
}

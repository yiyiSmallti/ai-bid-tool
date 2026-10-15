package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func nativeConsumerResult(ok bool, command string, data Object) Object {
	return Object{
		"ok": ok, "command": command, "data": data,
		"items": []Object{}, "warnings": []string{}, "duration_ms": 0,
		"cost": Object{"calls": 0, "llm_tokens": 0, "ocr_pages": 0, "search_requests": 0, "browser_units": 0, "unresolved_calls": 0},
	}
}

func TestNativeClientPartialReportDoesNotAuthorizeFailedMutation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/scores/saved" {
			_ = json.NewEncoder(w).Encode(nativeConsumerResult(false, "score show", Object{"total_status": "unavailable", "total": nil}))
			return
		}
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(nativeConsumerResult(false, "card confirm", Object{
			"error":            Object{"code": "revision_conflict", "message": "Revision changed"},
			"current_revision": 2,
		}))
	}))
	defer server.Close()
	client := NewAPIClient(server.URL)
	partial, err := client.Request(context.Background(), "GET", "/scores/saved", nil)
	if err != nil || partial.OK || str(partial.Data, "total_status") != "unavailable" || partial.Data["total"] != nil {
		t.Fatalf("partial report must remain readable without inventing a score: %v", err)
	}
	_, err = client.Request(context.Background(), "POST", "/response-cards/card/confirm", Object{"expected_revision": 1})
	var problem *APIError
	if !errors.As(err, &problem) || problem.Status != http.StatusConflict || problem.Code != "revision_conflict" || integer(problem.Result.Data, "current_revision") != 2 {
		t.Fatalf("failed confirmation must preserve its conflict receipt: %v", err)
	}
}

func TestNativeClientRevokedSessionIsNotReused(t *testing.T) {
	for _, operation := range []string{"request", "binary", "events"} {
		t.Run(operation, func(t *testing.T) {
			authorizations := make(chan string, 2)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				authorizations <- r.Header.Get("Authorization")
				w.Header().Set("Content-Type", "application/json")
				if r.URL.Path == "/denied" {
					w.WriteHeader(http.StatusUnauthorized)
					_ = json.NewEncoder(w).Encode(nativeConsumerResult(false, "session", Object{"error": Object{"code": "session_required", "message": "Session expired"}}))
					return
				}
				_ = json.NewEncoder(w).Encode(nativeConsumerResult(true, "task list", Object{}))
			}))
			defer server.Close()
			client := NewAPIClient(server.URL)
			client.SetSession("synthetic-native-session")
			var err error
			switch operation {
			case "request":
				_, err = client.Request(context.Background(), "GET", "/denied", nil)
			case "binary":
				_, err = client.Binary(context.Background(), "/denied")
			case "events":
				err = client.Events(context.Background(), "/denied", "saved-cursor", func(Object) { t.Error("denied stream delivered an event") })
			}
			var problem *APIError
			if !errors.As(err, &problem) || problem.Status != http.StatusUnauthorized {
				t.Fatalf("revocation must reach the native page as authorization loss: %v", err)
			}
			if _, err := client.Request(context.Background(), "GET", "/after", nil); err != nil {
				t.Fatal(err)
			}
			if <-authorizations != "Bearer synthetic-native-session" || <-authorizations != "" {
				t.Fatal("revoked session was retained or the initial authenticated request was missing")
			}
		})
	}
}

func TestNativeClientDoesNotForwardSessionToAnotherOrigin(t *testing.T) {
	var foreignRequests atomic.Int64
	foreign := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		foreignRequests.Add(1)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer foreign.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, foreign.URL+"/collect", http.StatusTemporaryRedirect)
	}))
	defer origin.Close()
	client := NewAPIClient(origin.URL)
	client.SetSession("synthetic-native-session")
	for _, path := range []string{foreign.URL + "/collect", "//" + strings.TrimPrefix(foreign.URL, "http://") + "/collect", "/\\collect", "/tasks#fragment", "/redirect"} {
		if _, err := client.Request(context.Background(), "GET", path, nil); err == nil {
			t.Fatalf("untrusted destination accepted: %q", path)
		}
	}
	if foreignRequests.Load() != 0 {
		t.Fatal("native client followed an untrusted destination")
	}
}

func TestNativeProviderWriteDiscardsCredentialBearingReceipts(t *testing.T) {
	const secret = "synthetic-credential-sentinel"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(Object{"ok": false, "data": Object{
			"api_key": secret, "key_suffix": "sentinel",
			"error": Object{"code": "unknown_error", "message": secret},
		}})
	}))
	defer server.Close()
	body := Object{"api_key": secret}
	result, err := NewAPIClient(server.URL).ProviderWrite(context.Background(), body)
	if err == nil || strings.Contains(err.Error(), secret) || result.Data != nil {
		t.Fatal("provider failure must expose only a safe error, without receipt fields")
	}
	if _, retained := body["api_key"]; retained {
		t.Fatal("submitted provider credential remained in the editable request object")
	}
}

func TestNativeClientRejectsTrailingResponseData(t *testing.T) {
	for _, scenario := range []struct {
		name, tail string
		wantError  bool
	}{
		{"another-json-value", "{}", true},
		{"garbage", "not-json", true},
		{"whitespace", " \n\t", false},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_ = json.NewEncoder(w).Encode(nativeConsumerResult(true, "task list", Object{}))
				_, _ = io.WriteString(w, scenario.tail)
			}))
			defer server.Close()
			_, err := NewAPIClient(server.URL).Request(context.Background(), "GET", "/tasks", nil)
			if (err != nil) != scenario.wantError {
				t.Fatalf("complete response validation: got error %v, want rejection %v", err, scenario.wantError)
			}
		})
	}
}

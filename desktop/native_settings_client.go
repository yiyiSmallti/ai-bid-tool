package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

var settingsUUIDPattern = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var settingsFieldKeyPattern = regexp.MustCompile(`^[a-z][a-z0-9_]{1,47}$`)
var settingsCredentialNamePattern = regexp.MustCompile(`^[a-z0-9_]{1,40}$`)
var settingsFingerprintPattern = regexp.MustCompile(`^sha256:[a-f0-9]{16}$`)
var settingsSHA256Pattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var settingsLevelNamePattern = regexp.MustCompile(`^[a-z0-9_-]{1,20}$`)
var settingsEffortPattern = regexp.MustCompile(`^[a-z]{1,10}$`)

func settingsUUID(value string) bool { return settingsUUIDPattern.MatchString(value) }
func settingsTime(value any) bool {
	text, ok := value.(string)
	if !ok {
		return false
	}
	_, err := time.Parse(time.RFC3339Nano, text)
	return err == nil
}
func settingsKeys(value Object, keys ...string) bool {
	if len(value) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, ok := value[key]; !ok {
			return false
		}
	}
	return true
}
func settingsString(value any, min, max int) bool {
	text, ok := value.(string)
	if !ok {
		return false
	}
	n := utf8.RuneCountInString(text)
	return n >= min && n <= max
}
func settingsInteger(value any, min, max int) bool {
	n, ok := value.(float64)
	if !ok {
		if number, ok := value.(int); ok {
			return number >= min && number <= max
		}
		return false
	}
	return n >= float64(min) && n <= float64(max) && n == float64(int64(n))
}
func settingsBoolean(value any) bool { _, ok := value.(bool); return ok }
func settingsInvalid() error {
	return &APIError{Status: 502, Code: "invalid_response", Message: settingsFailures["invalid_response"]}
}
func settingsHTTPS(value any) bool {
	if value == nil {
		return true
	}
	text, ok := value.(string)
	if !ok || len(text) > 300 || strings.ContainsAny(text, "\\\t\r\n ") {
		return false
	}
	u, err := url.Parse(text)
	return err == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.RawQuery == "" && u.Fragment == ""
}
func settingsObjectArray(value any, maximum int) bool {
	items, ok := value.([]any)
	if !ok || len(items) > maximum {
		return false
	}
	for _, item := range items {
		if object(item) == nil {
			return false
		}
	}
	return true
}

type settingsEnvelope struct {
	OK    bool              `json:"ok"`
	Data  json.RawMessage   `json:"data"`
	Items []json.RawMessage `json:"items"`
}

// This boundary parses only the protected settings projections. It delegates
// connection/authentication to send and never retains server errors or warnings.
func (c *APIClient) settingsWire(ctx context.Context, method, path string, body Object) (settingsEnvelope, int, error) {
	var encoded []byte
	var reader io.Reader
	contentType := ""
	if body != nil {
		var err error
		encoded, err = json.Marshal(body)
		delete(body, "api_key")
		delete(body, "value")
		if err != nil {
			return settingsEnvelope{}, 0, settingsSafeError(err)
		}
		defer clear(encoded)
		reader = bytes.NewReader(encoded)
		contentType = "application/json"
	}
	response, err := c.send(ctx, method, path, reader, contentType)
	if err != nil {
		return settingsEnvelope{}, 0, settingsSafeError(err)
	}
	defer response.Body.Close()
	if response.StatusCode == 401 {
		c.SetSession("")
	}
	if response.ContentLength > 1<<20 {
		return settingsEnvelope{}, response.StatusCode, settingsInvalid()
	}
	var wire settingsEnvelope
	limited := &io.LimitedReader{R: response.Body, N: (1 << 20) + 1}
	decoder := json.NewDecoder(limited)
	if err := decoder.Decode(&wire); err != nil || limited.N <= 0 {
		return settingsEnvelope{}, response.StatusCode, settingsInvalid()
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return settingsEnvelope{}, response.StatusCode, settingsInvalid()
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 || !wire.OK {
		var failure struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
			Probe json.RawMessage `json:"probe"`
		}
		_ = json.Unmarshal(wire.Data, &failure)
		api := &APIError{Status: response.StatusCode, Code: failure.Error.Code}
		if len(failure.Probe) > 0 {
			var probe Object
			if json.Unmarshal(failure.Probe, &probe) == nil && settingsCredentialProbe(probe) {
				api.Result = Result{Data: Object{"probe": probe}}
			}
		}
		return settingsEnvelope{}, response.StatusCode, settingsSafeError(api)
	}
	return wire, response.StatusCode, nil
}

func settingsCredential(value Object) bool {
	if !settingsKeys(value, "name", "purpose", "provider", "endpoint", "id", "revision", "secret_version", "state", "fingerprint", "last_four", "created_at", "updated_at", "consumers") || !settingsUUID(str(value, "id")) || !settingsString(value["name"], 1, 40) || !settingsCredentialNamePattern.MatchString(str(value, "name")) || !settingsInteger(value["revision"], 1, 1<<30) || !settingsInteger(value["secret_version"], 1, 1<<30) || !containsString([]string{"active", "disabled", "removed"}, str(value, "state")) || !settingsFingerprintPattern.MatchString(str(value, "fingerprint")) || !settingsTime(value["created_at"]) || !settingsTime(value["updated_at"]) || !settingsString(value["endpoint"], 8, 300) || !settingsHTTPS(value["endpoint"]) || !settingsString(value["last_four"], 4, 4) {
		return false
	}
	last := str(value, "last_four")
	if len(last) != 4 {
		return false
	}
	for _, b := range []byte(last) {
		if b < 33 || b > 126 {
			return false
		}
	}
	purpose, provider, endpoint := str(value, "purpose"), str(value, "provider"), str(value, "endpoint")
	switch purpose {
	case "standalone_llm":
		if provider != "anthropic" && provider != "openai" {
			return false
		}
	case "vendor_search":
		if provider != "perplexity" || endpoint != "https://api.perplexity.ai" {
			return false
		}
	case "clef_workers_ai":
		if provider != "cloudflare" || endpoint != "https://gateway.ai.cloudflare.com" {
			return false
		}
	case "clef_gateway":
		if provider != "cloudflare" || endpoint != "https://api.cloudflare.com/client/v4" {
			return false
		}
	default:
		return false
	}
	if !settingsObjectArray(value["consumers"], 4) {
		return false
	}
	seen := make(map[string]bool, 4)
	for _, consumer := range objects(value["consumers"]) {
		service := str(consumer, "service")
		if !settingsKeys(consumer, "kind", "service", "selected") || str(consumer, "kind") != "service" || !containsString([]string{"standalone_llm", "vendor_search", "clef_workers_ai", "clef_gateway"}, service) || !settingsBoolean(consumer["selected"]) || seen[service] {
			return false
		}
		seen[service] = true
	}
	return true
}
func settingsCredentialProbe(value Object) bool {
	return settingsKeys(value, "probe_id", "credential_id", "tested_revision", "secret_version", "outcome", "duration_ms", "checked_at", "retry_after_seconds", "proves") && settingsUUID(str(value, "probe_id")) && settingsUUID(str(value, "credential_id")) && settingsInteger(value["tested_revision"], 1, 1<<30) && settingsInteger(value["secret_version"], 1, 1<<30) && containsString([]string{"passed", "auth_failed", "unsupported", "timeout", "rate_limited", "unavailable", "interrupted"}, str(value, "outcome")) && settingsInteger(value["duration_ms"], 0, 1<<30) && settingsTime(value["checked_at"]) && (value["retry_after_seconds"] == nil || settingsInteger(value["retry_after_seconds"], 0, 3600)) && str(value, "proves") == "authentication_only"
}
func (c *APIClient) CredentialRequest(ctx context.Context, method, path string, body Object) (Result, error) {
	u, err := url.Parse(path)
	if err != nil || (u.Path != "/platform/credentials" && !strings.HasPrefix(u.Path, "/platform/credentials/")) || (method != "GET" && method != "POST") {
		if body != nil {
			delete(body, "api_key")
		}
		return Result{}, settingsInvalid()
	}
	wire, _, err := c.settingsWire(ctx, method, path, body)
	if err != nil {
		return Result{}, err
	}
	var data Object
	if json.Unmarshal(wire.Data, &data) != nil {
		return Result{}, settingsInvalid()
	}
	result := Result{OK: true, Data: data}
	if method == "GET" && u.Path == "/platform/credentials" {
		if !settingsKeys(data, "next_after_name") || data["next_after_name"] != nil && !settingsCredentialNamePattern.MatchString(str(data, "next_after_name")) || len(wire.Items) > 100 {
			return Result{}, settingsInvalid()
		}
		seen := make(map[string]bool, len(wire.Items))
		for _, raw := range wire.Items {
			var row Object
			if json.Unmarshal(raw, &row) != nil || !settingsCredential(row) || seen[str(row, "id")] {
				return Result{}, settingsInvalid()
			}
			seen[str(row, "id")] = true
			result.Items = append(result.Items, row)
		}
	} else if strings.HasSuffix(u.Path, "/test") {
		if !settingsKeys(data, "probe") || !settingsCredentialProbe(object(data["probe"])) || len(wire.Items) != 0 {
			return Result{}, settingsInvalid()
		}
	} else if !settingsKeys(data, "credential") || !settingsCredential(object(data["credential"])) || len(wire.Items) != 0 {
		return Result{}, settingsInvalid()
	}
	return result, nil
}

func settingsConfidentialBase(value Object) bool {
	return settingsFieldKeyPattern.MatchString(str(value, "key")) && str(value, "placeholder") == "{{secret."+str(value, "key")+"}}" && settingsString(value["label"], 1, 100) && strings.TrimSpace(str(value, "label")) != "" && containsString([]string{"amount", "contact", "identity", "bank_account", "other"}, str(value, "kind")) && containsString([]string{"library", "task"}, str(value, "scope"))
}
func settingsConfidentialField(value Object) bool {
	return settingsKeys(value, "id", "key", "placeholder", "label", "kind", "scope", "archived", "revision", "created_at") && settingsConfidentialBase(value) && settingsUUID(str(value, "id")) && settingsBoolean(value["archived"]) && settingsInteger(value["revision"], 1, 1<<30) && settingsTime(value["created_at"])
}
func settingsConfidentialValue(value Object, task, field string) bool {
	if !settingsKeys(value, "field_id", "key", "placeholder", "label", "kind", "scope", "task_id", "status", "value_id", "version", "tail", "set_at") || !settingsConfidentialBase(value) || !settingsUUID(str(value, "field_id")) || field != "" && str(value, "field_id") != field {
		return false
	}
	if str(value, "scope") == "task" {
		if !settingsUUID(task) || str(value, "task_id") != task {
			return false
		}
	} else if value["task_id"] != nil {
		return false
	}
	if str(value, "status") == "missing" {
		return value["value_id"] == nil && value["version"] == nil && value["tail"] == nil && value["set_at"] == nil
	}
	if str(value, "status") != "filled" || !settingsUUID(str(value, "value_id")) || !settingsInteger(value["version"], 1, 1<<30) || !settingsTime(value["set_at"]) {
		return false
	}
	if value["tail"] != nil {
		tail := str(value, "tail")
		if !containsString([]string{"contact", "identity", "bank_account"}, str(value, "kind")) || utf8.RuneCountInString(tail) != 4 {
			return false
		}
		for _, r := range tail {
			if !unicode.IsLetter(r) && !unicode.IsNumber(r) {
				return false
			}
		}
	}
	return true
}
func settingsConfidentialValueForField(value Object, task string, field Object) bool {
	if !settingsConfidentialField(field) || !settingsConfidentialValue(value, task, str(field, "id")) {
		return false
	}
	for _, key := range []string{"key", "placeholder", "kind", "scope"} {
		if value[key] != field[key] {
			return false
		}
	}
	return true
}
func settingsPageMeta(value Object, count, maximum int) bool {
	if !settingsKeys(value, "as_of", "returned", "next_cursor", "has_more") || !settingsTime(value["as_of"]) || integer(value, "returned") != count || count > maximum || !settingsBoolean(value["has_more"]) {
		return false
	}
	if value["next_cursor"] != nil && !settingsString(value["next_cursor"], 1, 2048) {
		return false
	}
	return mgBool(value["has_more"]) == (value["next_cursor"] != nil)
}
func (c *APIClient) ConfidentialRequest(ctx context.Context, method, path string, body Object) (Result, error) {
	u, err := url.Parse(path)
	allowed := err == nil && (u.Path == "/confidential-fields" || u.Path == "/confidential-values" || strings.HasPrefix(u.Path, "/confidential-fields/") || strings.HasPrefix(u.Path, "/management/confidential-fields/") || u.Path == "/management/confidential-fields/query" || u.Path == "/management/confidential-values/query") && !strings.HasSuffix(u.Path, "/reveal") && (method == "GET" || method == "POST")
	if !allowed {
		if body != nil {
			delete(body, "value")
		}
		return Result{}, settingsInvalid()
	}
	task := u.Query().Get("task_id")
	if body != nil && body["task_id"] != nil {
		task = str(body, "task_id")
	}
	field := ""
	if body != nil {
		field = str(body, "field_id")
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if len(parts) >= 3 && parts[0] == "confidential-fields" && settingsUUID(parts[1]) {
		field = parts[1]
	}
	if len(parts) >= 4 && parts[0] == "management" && parts[1] == "confidential-fields" && settingsUUID(parts[2]) {
		field = parts[2]
	}
	history := strings.HasSuffix(u.Path, "/values/history/query") || method == "GET" && strings.HasSuffix(u.Path, "/values")
	wire, _, err := c.settingsWire(ctx, method, path, body)
	if err != nil {
		return Result{}, err
	}
	var data Object
	if json.Unmarshal(wire.Data, &data) != nil {
		return Result{}, settingsInvalid()
	}
	isValues := strings.Contains(u.Path, "/values") || strings.Contains(u.Path, "confidential-values")
	isList := method == "GET" || strings.HasSuffix(u.Path, "/query")
	result := Result{OK: true, Data: data}
	if isList {
		if strings.HasPrefix(u.Path, "/management/") {
			if !settingsPageMeta(data, len(wire.Items), 25) || len(wire.Data) > 256<<10 {
				return Result{}, settingsInvalid()
			}
		} else if u.Path == "/confidential-values" {
			if !settingsKeys(data, "task_id", "missing") || str(data, "task_id") != task || !settingsInteger(data["missing"], 0, 1<<30) {
				return Result{}, settingsInvalid()
			}
		} else if !settingsKeys(data, "count") || integer(data, "count") != len(wire.Items) {
			return Result{}, settingsInvalid()
		}
		seen := make(map[string]bool, len(wire.Items))
		for _, raw := range wire.Items {
			var row Object
			if json.Unmarshal(raw, &row) != nil || isValues && (!settingsConfidentialValue(row, task, field) || history && str(row, "status") != "filled") || !isValues && (!settingsConfidentialField(row) || field != "" && str(row, "id") != field) {
				return Result{}, settingsInvalid()
			}
			id := str(row, "id")
			if isValues {
				id = str(row, "field_id")
				if strings.Contains(u.Path, "/values/") || strings.HasSuffix(u.Path, "/values") {
					id = str(row, "value_id")
				}
			}
			if seen[id] {
				return Result{}, settingsInvalid()
			}
			seen[id] = true
			result.Items = append(result.Items, row)
		}
	} else if len(wire.Items) != 0 || isValues && !settingsConfidentialValue(data, task, field) || !isValues && !settingsConfidentialField(data) {
		return Result{}, settingsInvalid()
	}
	return result, nil
}
func (c *APIClient) RevealConfidential(ctx context.Context, valueID, fieldID, key string) (string, error) {
	if !settingsUUID(valueID) || !settingsUUID(fieldID) || !settingsFieldKeyPattern.MatchString(key) {
		return "", settingsInvalid()
	}
	wire, _, err := c.settingsWire(ctx, "POST", "/confidential-values/"+url.PathEscape(valueID)+"/reveal", nil)
	if err != nil {
		return "", err
	}
	var value Object
	if json.Unmarshal(wire.Data, &value) != nil || len(wire.Items) != 0 || !settingsKeys(value, "field_id", "value_id", "key", "value") || str(value, "field_id") != fieldID || str(value, "value_id") != valueID || str(value, "key") != key || !settingsString(value["value"], 1, 2000) {
		return "", settingsInvalid()
	}
	return str(value, "value"), nil
}

func settingsProviderChoices(value any, defaultName any, checkDefault bool) bool {
	if !settingsObjectArray(value, 8) {
		return false
	}
	seen := make(map[string]bool, 8)
	for _, level := range objects(value) {
		name := str(level, "name")
		if !settingsKeys(level, "name", "label") || !settingsString(level["name"], 1, 20) || !settingsLevelNamePattern.MatchString(name) || seen[name] || level["label"] != nil && !settingsString(level["label"], 0, 100) {
			return false
		}
		seen[name] = true
	}
	if !checkDefault {
		return true
	}
	if defaultName == nil {
		return len(seen) == 0
	}
	name, ok := defaultName.(string)
	return ok && seen[name]
}
func settingsProviderMetadata(value Object, id string) bool {
	if !settingsKeys(value, "id", "slot", "name", "revision", "configuration", "provider", "model", "credential_state", "updated_at", "revised_at", "reasoning", "default_reasoning") || !settingsUUID(str(value, "id")) || !settingsUUID(str(value, "slot")) || value["name"] != nil && !settingsString(value["name"], 1, 60) || id != "" && str(value, "id") != id || !settingsInteger(value["revision"], 1, 1<<30) || !containsString([]string{"anthropic", "openai"}, str(value, "provider")) || !settingsString(value["model"], 1, 100) || str(value, "credential_state") != "configured" || !settingsTime(value["updated_at"]) || !settingsTime(value["revised_at"]) {
		return false
	}
	configuration := object(value["configuration"])
	if !settingsKeys(configuration, "capability", "slot", "new", "name", "provider", "model", "base_url", "json_mode", "reasoning", "default_reasoning", "expected_revision") || str(configuration, "capability") != "llm_extract" || configuration["expected_revision"] != nil || str(configuration, "slot") != str(value, "slot") || configuration["new"] != false || configuration["name"] != value["name"] || str(configuration, "provider") != str(value, "provider") || !settingsString(configuration["model"], 1, 100) || str(configuration, "model") != str(value, "model") || !settingsHTTPS(configuration["base_url"]) || !containsString([]string{"json_schema", "json_object"}, str(configuration, "json_mode")) || !settingsObjectArray(configuration["reasoning"], 8) {
		return false
	}
	for _, level := range objects(configuration["reasoning"]) {
		if !settingsKeys(level, "name", "label", "request_options", "effort", "batch_chars") || !settingsLevelNamePattern.MatchString(str(level, "name")) || level["label"] != nil && !settingsString(level["label"], 0, 60) || object(level["request_options"]) == nil || level["effort"] != nil && !settingsEffortPattern.MatchString(str(level, "effort")) || !settingsInteger(level["batch_chars"], 1000, 200000) {
			return false
		}
	}
	return settingsProviderChoices(value["reasoning"], value["default_reasoning"], true)
}
func settingsProviderSettings(value Object) bool {
	if !settingsKeys(value, "capability", "configured", "current", "models", "default_slot", "reasoning", "actions", "connection_status") || !settingsObjectArray(value["models"], 20) || value["default_slot"] != nil && str(value, "default_slot") != str(object(value["current"]), "slot") || value["default_slot"] == nil && value["current"] != nil || str(value, "capability") != "llm_extract" || str(value, "connection_status") != "not_checked" || !settingsBoolean(value["configured"]) || mgBool(value["configured"]) != (value["current"] != nil) || value["current"] != nil && !settingsProviderMetadata(object(value["current"]), "") || !settingsProviderChoices(value["reasoning"], nil, false) || !settingsObjectArray(value["actions"], 2) || len(objects(value["actions"])) != 2 {
		return false
	}
	slots := make(map[string]bool, 20)
	for _, model := range objects(value["models"]) {
		if !settingsProviderMetadata(model, "") || slots[str(model, "slot")] {
			return false
		}
		slots[str(model, "slot")] = true
	}
	if value["current"] != nil && !slots[str(object(value["current"]), "slot")] {
		return false
	}
	seen := make(map[string]bool, 2)
	for _, hint := range objects(value["actions"]) {
		action := str(hint, "action")
		if !settingsKeys(hint, "action", "allowed", "reason") || (action != "configure" && action != "test") || seen[action] || !settingsBoolean(hint["allowed"]) || mgBool(hint["allowed"]) && hint["reason"] != nil || !mgBool(hint["allowed"]) && !containsString([]string{"human_required", "provider_unavailable"}, str(hint, "reason")) {
			return false
		}
		seen[action] = true
	}
	return true
}

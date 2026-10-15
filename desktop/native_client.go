package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Object = map[string]any
type Result struct {
	OK         bool             `json:"ok"`
	Command    string           `json:"command"`
	Data       Object           `json:"data"`
	Items      []Object         `json:"items"`
	Warnings   []string         `json:"warnings"`
	Cost       map[string]int64 `json:"cost"`
	DurationMS int64            `json:"duration_ms"`
}
type APIError struct {
	Status        int
	Code, Message string
	Result        Result
	RetryAfter    string
}

func (e *APIError) Error() string { return e.Message }

type UploadFile struct{ Field, Path string }
type APIClient struct {
	BaseURL string
	HTTP    *http.Client
	mu      sync.RWMutex
	session string
}

func NewAPIClient(base string) *APIClient {
	return &APIClient{BaseURL: strings.TrimRight(base, "/"), HTTP: &http.Client{Timeout: 90 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("请求重定向已拒绝") }}}
}
func (c *APIClient) SetSession(s string) { c.mu.Lock(); c.session = s; c.mu.Unlock() }
func (c *APIClient) endpoint(path string) (string, error) {
	u, err := url.Parse(path)
	if err != nil || !strings.HasPrefix(path, "/") || strings.HasPrefix(path, "//") || strings.Contains(path, "\\") || u.IsAbs() || u.Host != "" || u.Fragment != "" {
		return "", errors.New("请求地址不受支持")
	}
	return c.BaseURL + path, nil
}
func (c *APIClient) send(ctx context.Context, method, path string, body io.Reader, contentType string) (*http.Response, error) {
	endpoint, err := c.endpoint(path)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, method, endpoint, body)
	if err != nil {
		return nil, err
	}
	c.mu.RLock()
	token := c.session
	c.mu.RUnlock()
	if token != "" && path != "/auth/session" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	req.Header.Set("Cache-Control", "no-store")
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	response, err := c.HTTP.Do(req)
	if err == nil && response.StatusCode == 401 {
		c.SetSession("")
	}
	return response, err
}
func decodeResult(response *http.Response) (Result, error) {
	defer response.Body.Close()
	var result Result
	var raw map[string]json.RawMessage
	dec := json.NewDecoder(io.LimitReader(response.Body, 32<<20))
	if err := dec.Decode(&raw); err != nil {
		return result, errors.New("服务返回了无法识别的内容")
	}
	var trailing struct{}
	if err := dec.Decode(&trailing); err != io.EOF {
		return result, errors.New("服务响应包含多余内容")
	}
	keys := []string{"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
	if len(raw) != len(keys) {
		return result, errors.New("服务响应不符合 Result 5.0 契约")
	}
	for _, key := range keys {
		if _, ok := raw[key]; !ok {
			return result, errors.New("服务响应缺少 " + key)
		}
	}
	fields := []any{&result.OK, &result.Command, &result.Data, &result.Items, &result.Warnings, &result.Cost, &result.DurationMS}
	for i, key := range keys {
		if bytes.Equal(bytes.TrimSpace(raw[key]), []byte("null")) {
			return result, errors.New("服务响应字段不能为空")
		}
		if err := json.Unmarshal(raw[key], fields[i]); err != nil {
			return result, errors.New("服务响应字段不符合 Result 5.0 契约")
		}
	}
	if result.Command == "" || result.Data == nil || result.Items == nil || result.Warnings == nil || result.DurationMS < 0 || len(result.Cost) != 6 {
		return result, errors.New("服务响应不符合 Result 5.0 契约")
	}
	for _, key := range []string{"calls", "llm_tokens", "ocr_pages", "search_requests", "browser_units", "unresolved_calls"} {
		v, ok := result.Cost[key]
		if !ok || v < 0 {
			return result, errors.New("服务用量响应不符合 Result 5.0 契约")
		}
	}
	problem := object(result.Data["error"])
	partial := false
	switch result.Command {
	case "check show", "score show", "score rubric show":
		partial = problem == nil && (str(object(result.Data["report"]), "completion") == "partial" || str(result.Data, "completion") == "partial" || str(result.Data, "total_status") == "unavailable" || str(result.Data, "total_status") == "range_only" || (isString(result.Data["snapshot"]) && isString(result.Data["parent_id"]) && isInteger(result.Data["returned"])))
	case "check list", "score list", "score rubric list":
		partial = problem == nil && isString(result.Data["task_id"]) && isInteger(result.Data["total"]) && len(result.Items) > 0
		hasPartial := false
		for _, item := range result.Items {
			report := object(item["report"])
			id := item["id"]
			if report != nil {
				id = report["id"]
			}
			partial = partial && isString(id)
			hasPartial = hasPartial || str(report, "completion") == "partial" || str(item, "total_status") == "unavailable" || str(item, "total_status") == "range_only"
		}
		partial = partial && hasPartial
	case "review show":
		partial = problem == nil && str(object(result.Data["run"]), "completion") == "partial" && isString(object(result.Data["run"])["id"]) && isArray(result.Data["obligations"]) && isArray(result.Data["signing_requirements"])
	case "review report show", "review report list":
		partial = problem == nil && str(result.Data, "completion") == "partial" && isString(result.Data["review_id"])
	case "draft show":
		partial = str(result.Data, "completion") == "partial"
	case "job status":
		kind, status := str(result.Data, "kind"), str(result.Data, "status")
		jobError := object(result.Data["error"])
		partialResult := str(object(result.Data["result"]), "completion") == "partial"
		assessment := containsString([]string{"check", "score_rubric", "score", "annotation_render", "annotation_release", "memory_candidate", "bid_review_prepare", "bid_review", "bid_review_report"}, kind)
		partial = assessment && ((status == "succeeded" && partialResult) || (status == "cancelled" && (result.Data["error"] == nil || str(jobError, "code") != "")) || (status == "failed" && str(jobError, "code") != "" && integer(jobError, "exit_code") >= 2 && integer(jobError, "exit_code") <= 5))
		partial = partial || (partialResult && containsString([]string{"draft", "card_generate", "memory_candidate"}, kind))
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 || (!result.OK && !partial) {
		message := str(problem, "message")
		if message == "" {
			message = "操作未完成，请核对当前状态"
		}
		return result, &APIError{Status: response.StatusCode, Code: str(problem, "code"), Message: message, Result: result, RetryAfter: response.Header.Get("Retry-After")}
	}
	return result, nil
}
func (c *APIClient) Request(ctx context.Context, method, path string, body any) (Result, error) {
	var reader io.Reader
	contentType := ""
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return Result{}, err
		}
		reader = bytes.NewReader(data)
		contentType = "application/json"
	}
	response, err := c.send(ctx, method, path, reader, contentType)
	if err != nil {
		return Result{}, err
	}
	if response.StatusCode == 401 {
		c.SetSession("")
	}
	return decodeResult(response)
}
func (c *APIClient) Exchange(ctx context.Context, key string) error {
	r, err := c.Request(ctx, "POST", "/auth/session", Object{"access_key": key})
	if err != nil {
		return err
	}
	s := str(r.Data, "session")
	if s == "" || integer(r.Data, "expires_in") <= 0 {
		return errors.New("会话响应不符合契约")
	}
	c.SetSession(s)
	return nil
}
func (c *APIClient) Upload(ctx context.Context, path string, fields map[string]string, files []UploadFile) (Result, error) {
	reader, writer := io.Pipe()
	defer reader.Close()
	multipartWriter := multipart.NewWriter(writer)
	go func() {
		var err error
		defer func() {
			if err == nil {
				err = multipartWriter.Close()
			}
			_ = writer.CloseWithError(err)
		}()
		for k, v := range fields {
			if err = multipartWriter.WriteField(k, v); err != nil {
				return
			}
		}
		for _, file := range files {
			var f *os.File
			f, err = os.Open(file.Path)
			if err != nil {
				return
			}
			var part io.Writer
			part, err = multipartWriter.CreateFormFile(file.Field, filepath.Base(file.Path))
			if err == nil {
				_, err = io.Copy(part, f)
			}
			_ = f.Close()
			if err != nil {
				return
			}
		}
	}()
	response, err := c.send(ctx, "POST", path, reader, multipartWriter.FormDataContentType())
	if err != nil {
		_ = reader.CloseWithError(err)
		return Result{}, err
	}
	return decodeResult(response)
}
func (c *APIClient) Binary(ctx context.Context, path string) ([]byte, error) {
	response, err := c.send(ctx, "GET", path, nil, "")
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, err = decodeResult(response)
		return nil, err
	}
	defer response.Body.Close()
	const limit = 128 << 20
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		return nil, err
	}
	if len(data) > limit {
		return nil, errors.New("文件超过原生预览下载上限")
	}
	if response.ContentLength >= 0 && int64(len(data)) != response.ContentLength {
		return nil, errors.New("文件下载长度不符")
	}
	return data, nil
}
func (c *APIClient) Events(ctx context.Context, path, cursor string, onMessage func(Object)) error {
	endpoint, err := c.endpoint(path)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, "GET", endpoint, nil)
	if err != nil {
		return err
	}
	c.mu.RLock()
	token := c.session
	c.mu.RUnlock()
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "text/event-stream")
	if cursor != "" {
		req.Header.Set("Last-Event-ID", cursor)
	}
	client := *c.HTTP
	client.Timeout = 0
	response, err := client.Do(req)
	if err != nil {
		return err
	}
	if response.StatusCode == 401 {
		c.SetSession("")
	}
	if response.StatusCode != 200 {
		_, err = decodeResult(response)
		return err
	}
	defer response.Body.Close()
	if !strings.HasPrefix(response.Header.Get("Content-Type"), "text/event-stream") {
		return errors.New("进度流格式不符合契约")
	}
	scanner := bufio.NewScanner(response.Body)
	scanner.Buffer(make([]byte, 8192), 8192)
	var data strings.Builder
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if data.Len() > 0 {
				var event Object
				if err := json.Unmarshal([]byte(data.String()), &event); err != nil {
					return errors.New("进度数据无法识别")
				}
				onMessage(event)
				data.Reset()
			}
			continue
		}
		if strings.HasPrefix(line, "data:") {
			if data.Len() > 0 {
				data.WriteByte('\n')
			}
			data.WriteString(strings.TrimSpace(strings.TrimPrefix(line, "data:")))
			if data.Len() > 4096 {
				return errors.New("进度数据超过上限")
			}
		}
	}
	return scanner.Err()
}
func object(v any) Object { m, _ := v.(map[string]any); return m }
func objects(v any) []Object {
	if list, ok := v.([]Object); ok {
		return list
	}
	list, _ := v.([]any)
	out := make([]Object, 0, len(list))
	for _, v := range list {
		if m := object(v); m != nil {
			out = append(out, m)
		}
	}
	return out
}
func str(m Object, key string) string {
	v := m[key]
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprint(v)
}
func integer(m Object, key string) int {
	switch v := m[key].(type) {
	case float64:
		return int(v)
	case int:
		return v
	case json.Number:
		n, _ := v.Int64()
		return int(n)
	}
	return 0
}
func isString(v any) bool  { _, ok := v.(string); return ok }
func isArray(v any) bool   { _, ok := v.([]any); return ok }
func isInteger(v any) bool { n, ok := v.(float64); return ok && n >= 0 && n == float64(int64(n)) }
func containsString(values []string, value string) bool {
	for _, v := range values {
		if v == value {
			return true
		}
	}
	return false
}

// Provider writes deliberately discard suffix-bearing receipts and arbitrary errors.
func (c *APIClient) ProviderWrite(ctx context.Context, body any) (Result, error) {
	data, err := json.Marshal(body)
	if fields, ok := body.(Object); ok {
		delete(fields, "api_key")
	}
	if err != nil {
		return Result{}, errors.New("配置参数无法序列化")
	}
	response, err := c.send(ctx, "POST", "/providers", bytes.NewReader(data), "application/json")
	if err != nil {
		return Result{}, errors.New("保存结果无法核验，请重新读取配置")
	}
	defer response.Body.Close()
	var receipt struct {
		OK   bool `json:"ok"`
		Data struct {
			ID       string `json:"id"`
			Revision int    `json:"revision"`
			Error    struct {
				Code string `json:"code"`
			} `json:"error"`
		} `json:"data"`
	}
	err = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&receipt)
	if err == nil && response.StatusCode >= 200 && response.StatusCode < 300 && receipt.OK && len(receipt.Data.ID) == 36 && receipt.Data.Revision > 0 {
		return Result{OK: true, Data: Object{"id": receipt.Data.ID, "revision": receipt.Data.Revision}}, nil
	}
	messages := map[string]string{"revision_conflict": "修订已变化，请核对当前配置", "provider_key_required": "首次设置或更换供应商、端点需输入新密钥", "invalid_input": "配置参数无效", "forbidden": "当前身份无权维护配置", "human_required": "需要本人操作", "not_found": "模型不可访问", "session_required": "请重新输入访问密钥", "provider_model_limit": "最多保存 20 个模型，请先删除不用的模型", "provider_model_unavailable": "该模型已删除或不存在，请重新读取"}
	code := receipt.Data.Error.Code
	message, ok := messages[code]
	if !ok {
		code = "provider_save_failed"
		message = "保存结果无法核验，请重新读取配置"
	}
	return Result{}, &APIError{Status: response.StatusCode, Code: code, Message: message}
}

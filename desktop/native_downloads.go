package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"mime"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/egoist/mygo"
)

// VerifiedDownload binds a signed URL to the exact immutable resource the page inspected.
func (c *APIClient) VerifiedDownload(ctx context.Context, linkPath, expectedPath string, metadata Object) ([]byte, error) {
	return c.verifiedDownload(ctx, "GET", linkPath, expectedPath, metadata)
}
func (c *APIClient) verifiedDownload(ctx context.Context, method, linkPath, expectedPath string, metadata Object) ([]byte, error) {
	result, err := c.Request(ctx, method, linkPath, nil)
	if err != nil {
		return nil, err
	}
	descriptor := result.Data
	parsed, err := url.Parse(str(descriptor, "url"))
	base, baseErr := url.Parse(c.BaseURL)
	if err != nil || baseErr != nil || parsed.Fragment != "" || parsed.Path != expectedPath || integer(descriptor, "expires_in") != 300 || len(parsed.Query()) != 1 || len(parsed.Query()["signature"]) != 1 || parsed.Query().Get("signature") == "" || parsed.Host != "" && (parsed.Scheme != base.Scheme || parsed.Host != base.Host) {
		return nil, errors.New("下载链接不属于所选精确资源")
	}
	fixed := Object{}
	for _, key := range []string{"file", "image", "artifact"} {
		if candidate := object(descriptor[key]); candidate != nil {
			for k, v := range candidate {
				fixed[k] = v
			}
			break
		}
	}
	for _, key := range []string{"sha256", "size_bytes", "media_type"} {
		if value := metadata[key]; value != nil && value != "" {
			if actual := fixed[key]; actual != nil && str(Object{"v": actual}, "v") != str(Object{"v": value}, "v") {
				return nil, errors.New("下载元数据与所选修订不一致")
			}
			fixed[key] = value
		}
	}
	response, err := c.send(ctx, "GET", parsed.RequestURI(), nil, "")
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, err = decodeResult(response)
		return nil, err
	}
	defer response.Body.Close()
	mediaType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil {
		return nil, errors.New("下载文件类型无法核验")
	}
	if expected := str(fixed, "media_type"); expected != "" && mediaType != expected {
		return nil, errors.New("下载文件类型与固定原件不同")
	}
	allowed := []string{"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/png", "image/jpeg"}
	if !containsString(allowed, mediaType) {
		return nil, errors.New("下载文件类型不符合契约")
	}
	const limit = 128 << 20
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		return nil, err
	}
	if len(data) > limit {
		return nil, errors.New("文件超过下载上限")
	}
	if response.ContentLength >= 0 && int64(len(data)) != response.ContentLength {
		return nil, errors.New("下载文件长度不完整")
	}
	if size := integer(fixed, "size_bytes"); size > 0 && len(data) != size {
		return nil, errors.New("下载文件大小与固定原件不一致")
	}
	if hash := str(fixed, "sha256"); hash != "" {
		expected, err := hex.DecodeString(hash)
		digest := sha256.Sum256(data)
		if err != nil || len(expected) != sha256.Size || !bytes.Equal(expected, digest[:]) {
			return nil, errors.New("下载文件 SHA-256 与固定原件不一致")
		}
	}
	if mediaType == "image/png" && !bytes.HasPrefix(data, []byte{137, 80, 78, 71, 13, 10, 26, 10}) {
		return nil, errors.New("下载内容不是有效 PNG")
	}
	if mediaType == "application/pdf" && !bytes.HasPrefix(data, []byte("%PDF-")) {
		return nil, errors.New("下载内容不是 PDF")
	}
	if mediaType == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" && !bytes.HasPrefix(data, []byte("PK\x03\x04")) {
		return nil, errors.New("下载内容不是 DOCX")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return data, nil
}

func completeDownloadMetadata(metadata Object) bool {
	hash, err := hex.DecodeString(str(metadata, "sha256"))
	media := str(metadata, "media_type")
	return err == nil && len(hash) == sha256.Size && integer(metadata, "size_bytes") > 0 && containsString([]string{"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/png", "image/jpeg"}, media)
}

// BinaryImage reads authenticated page previews, which have no signed-link contract.
func (c *APIClient) BinaryImage(ctx context.Context, path, expectedMIME string, metadata Object) ([]byte, error) {
	if expectedMIME != "image/png" && expectedMIME != "image/jpeg" {
		return nil, errors.New("预览图片类型不受支持")
	}
	response, err := c.send(ctx, "GET", path, nil, "")
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		_, err = decodeResult(response)
		return nil, err
	}
	defer response.Body.Close()
	media, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || media != expectedMIME {
		return nil, errors.New("预览图片类型不符")
	}
	const limit = 32 << 20
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil {
		return nil, err
	}
	if len(data) > limit || response.ContentLength >= 0 && int64(len(data)) != response.ContentLength {
		return nil, errors.New("预览图片长度不符")
	}
	if size := integer(metadata, "size_bytes"); size > 0 && size != len(data) {
		return nil, errors.New("预览图片与固定长度不符")
	}
	if hash := str(metadata, "sha256"); hash != "" {
		expected, err := hex.DecodeString(hash)
		actual := sha256.Sum256(data)
		if err != nil || len(expected) != sha256.Size || !bytes.Equal(expected, actual[:]) {
			return nil, errors.New("预览图片摘要不符")
		}
	}
	if media == "image/png" && !bytes.HasPrefix(data, []byte{137, 80, 78, 71, 13, 10, 26, 10}) || media == "image/jpeg" && !bytes.HasPrefix(data, []byte{255, 216, 255}) {
		return nil, errors.New("预览内容不是约定图片")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return data, nil
}

func (a *NativeApp) DownloadVerified(linkPath, expectedPath, filename string, metadata Object, inspected func()) {
	a.Do(func(ctx context.Context) (func(), error) {
		if inspected != nil && !completeDownloadMetadata(metadata) {
			return nil, errors.New("原件缺少可信摘要、长度或类型，不能完成审阅")
		}
		data, err := a.Client.VerifiedDownload(ctx, linkPath, expectedPath, metadata)
		if err != nil {
			return nil, err
		}
		var destination string
		if a.SaveDestination != nil {
			destination, err = a.SaveDestination(filepath.Base(filename))
		} else {
			destination, err = mygo.Dialog.Save(mygo.SaveDialogOptions{Parent: a.Window, Title: "保存文件", DefaultPath: filepath.Base(filename)})
		}
		if err != nil || destination == "" {
			return nil, err
		}
		if err = ctx.Err(); err != nil {
			return nil, err
		}
		file, err := os.CreateTemp(filepath.Dir(destination), ".aibid-download-*")
		if err != nil {
			return nil, err
		}
		temporary := file.Name()
		defer os.Remove(temporary)
		if err = file.Chmod(0600); err == nil {
			_, err = file.Write(data)
		}
		if err == nil {
			err = file.Sync()
		}
		closeErr := file.Close()
		if err == nil {
			err = closeErr
		}
		if err == nil {
			err = ctx.Err()
		}
		if err == nil {
			err = os.Rename(temporary, destination)
		}
		if err != nil {
			return nil, err
		}
		return func() {
			a.Notice = "文件已保存"
			if completeDownloadMetadata(metadata) {
				a.Notice = "文件类型、长度与固定摘要已核验，保存完成"
			}
			if inspected != nil {
				inspected()
			}
		}, nil
	})
}
func expectedDownloadPath(link string) string {
	return strings.ReplaceAll(strings.SplitN(link, "?", 2)[0], "download-link", "download")
}

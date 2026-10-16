package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// clientContract is the Result contract this client reads; a gateway is
// compatible when its contract has the same major version.
const clientContract = "5.0"

var contractPattern = regexp.MustCompile(`^([0-9]+)\.([0-9]+)$`)

// gatewayOrigin accepts only an HTTPS origin: a session never travels in clear
// text, and no path, query or credentials ride along with it.
func gatewayOrigin(raw string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Scheme != "https" {
		return "", errors.New("企业服务地址必须以 https:// 开头")
	}
	if u.Host == "" || u.User != nil || u.Opaque != "" || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
		return "", errors.New("企业服务地址只能包含主机名和端口，例如 https://gateway.example.com")
	}
	return "https://" + strings.ToLower(u.Host), nil
}

// UntrustedCertificateError reports a certificate the system does not trust.
// The user may add the gateway anyway by confirming Fingerprint.
type UntrustedCertificateError struct {
	Fingerprint string
	Reason      error
}

func (e *UntrustedCertificateError) Error() string {
	return "企业服务证书不受信任：" + e.Reason.Error()
}

// PinMismatchError reports a certificate other than the one pinned.
type PinMismatchError struct{ Fingerprint string }

func (e *PinMismatchError) Error() string {
	return "企业服务证书与已确认的指纹不一致，可能被替换或拦截"
}

func certificateFingerprint(cert *x509.Certificate) string {
	sum := sha256.Sum256(cert.Raw)
	return hex.EncodeToString(sum[:])
}

// formatFingerprint groups a fingerprint for the user to compare by eye.
func formatFingerprint(pin string) string {
	var parts []string
	for i := 0; i+2 <= len(pin); i += 2 {
		parts = append(parts, strings.ToUpper(pin[i:i+2]))
	}
	return strings.Join(parts, ":")
}

// gatewayTLS verifies against the system roots for host, or, with a pin,
// accepts only the pinned leaf certificate.
func gatewayTLS(host string, pin *string) *tls.Config {
	return &tls.Config{
		MinVersion: tls.VersionTLS12,
		// Verification happens in VerifyConnection, which can report the
		// fingerprint of a certificate the system refuses.
		InsecureSkipVerify: true,
		VerifyConnection: func(state tls.ConnectionState) error {
			if len(state.PeerCertificates) == 0 {
				return errors.New("企业服务未提供证书")
			}
			leaf := state.PeerCertificates[0]
			fingerprint := certificateFingerprint(leaf)
			if pin != nil {
				if fingerprint != *pin {
					return &PinMismatchError{Fingerprint: fingerprint}
				}
				return nil
			}
			intermediates := x509.NewCertPool()
			for _, cert := range state.PeerCertificates[1:] {
				intermediates.AddCert(cert)
			}
			if _, err := leaf.Verify(x509.VerifyOptions{DNSName: host, Intermediates: intermediates}); err != nil {
				return &UntrustedCertificateError{Fingerprint: fingerprint, Reason: err}
			}
			return nil
		},
	}
}

// NewGatewayClient talks only to origin, over the pinned or system-trusted TLS.
func NewGatewayClient(origin string, pin *string) *APIClient {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	host := strings.TrimPrefix(origin, "https://")
	if name, _, err := net.SplitHostPort(host); err == nil {
		host = name
	}
	transport.TLSClientConfig = gatewayTLS(strings.Trim(host, "[]"), pin)
	// The gateway is reached directly; a system proxy would see the session.
	transport.Proxy = nil
	client := NewAPIClient(origin)
	client.HTTP.Transport = transport
	return client
}

// GatewayInfo is what GET /installation reports of a gateway.
type GatewayInfo struct {
	Origin          string
	CompanyName     string
	ServerVersion   string
	ContractVersion string
}

// ProbeGateway reads an address's installation before it is added. It refuses
// a personal installation and an unreadable contract version.
func ProbeGateway(ctx context.Context, raw string, pin *string) (GatewayInfo, error) {
	origin, err := gatewayOrigin(raw)
	if err != nil {
		return GatewayInfo{}, err
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	result, err := NewGatewayClient(origin, pin).Request(ctx, "GET", "/installation", nil)
	if err != nil {
		var untrusted *UntrustedCertificateError
		var mismatch *PinMismatchError
		var apiError *APIError
		switch {
		case errors.As(err, &untrusted):
			return GatewayInfo{}, untrusted
		case errors.As(err, &mismatch):
			return GatewayInfo{}, mismatch
		case errors.As(err, &apiError):
			return GatewayInfo{}, fmt.Errorf("该地址不是企业服务：%s", apiError.Message)
		}
		return GatewayInfo{}, fmt.Errorf("无法连接企业服务：%w", err)
	}
	info := GatewayInfo{Origin: origin, CompanyName: str(result.Data, "company_name"), ServerVersion: str(result.Data, "server_version"), ContractVersion: str(result.Data, "contract_version")}
	if str(result.Data, "mode") != "enterprise" {
		return GatewayInfo{}, errors.New("该地址是个人安装，不是企业服务")
	}
	if !contractPattern.MatchString(info.ContractVersion) {
		return GatewayInfo{}, errors.New("无法识别企业服务的契约版本")
	}
	if info.CompanyName == "" || info.ServerVersion == "" {
		return GatewayInfo{}, errors.New("企业服务未报告公司名称或版本")
	}
	return info, nil
}

// contractProblem is empty for a compatible gateway contract, or what the user
// can do about an incompatible one.
func contractProblem(gateway string) string {
	theirs, ok := contractMajor(gateway)
	ours, _ := contractMajor(clientContract)
	switch {
	case !ok:
		return "无法识别企业服务的契约版本"
	case theirs > ours:
		return "请升级客户端"
	case theirs < ours:
		return "请联系管理员升级企业服务"
	}
	return ""
}

func contractMajor(version string) (int, bool) {
	match := contractPattern.FindStringSubmatch(version)
	if match == nil {
		return 0, false
	}
	major, err := strconv.Atoi(match[1])
	return major, err == nil
}

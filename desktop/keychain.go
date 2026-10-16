package main

import (
	"bytes"
	"errors"
	"os/exec"
	"regexp"
	"strings"
)

// SecretStore keeps one enterprise session per workspace id.
type SecretStore interface {
	Get(account string) (string, error) // "" when none is stored
	Set(account, secret string) error
	Delete(account string) error
}

const keychainService = "io.github.yiyismallti.aibid.session"

// security(1) exits with this status when no item matches.
const keychainNotFound = 44

// Session tokens and workspace ids are drawn from these characters, so they
// pass through security's command line without quoting.
var keychainSafe = regexp.MustCompile(`^[A-Za-z0-9._~+/=-]+$`)

// Keychain stores sessions in the user's login keychain. Commands go to
// security's interactive mode on stdin, so a session never appears in a
// process's arguments.
type Keychain struct{}

func (Keychain) run(command string) (string, int, error) {
	cmd := exec.Command("/usr/bin/security", "-i")
	cmd.Stdin = strings.NewReader(command + "\n")
	var stdout bytes.Buffer
	cmd.Stdout = &stdout
	err := cmd.Run()
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return "", exit.ExitCode(), nil
	}
	return stdout.String(), 0, err
}

func (k Keychain) Get(account string) (string, error) {
	if !keychainSafe.MatchString(account) {
		return "", errors.New("钥匙串账户名无效")
	}
	out, status, err := k.run("find-generic-password -s " + keychainService + " -a " + account + " -w")
	switch {
	case err != nil:
		return "", err
	case status == keychainNotFound:
		return "", nil
	case status != 0:
		return "", errors.New("无法读取钥匙串中的企业会话")
	}
	return strings.TrimSpace(out), nil
}

func (k Keychain) Set(account, secret string) error {
	if !keychainSafe.MatchString(account) || !keychainSafe.MatchString(secret) {
		return errors.New("企业会话格式无效，未写入钥匙串")
	}
	_, status, err := k.run("add-generic-password -U -s " + keychainService + " -a " + account + " -l AI-Bid-session -w " + secret)
	if err == nil && status != 0 {
		err = errors.New("无法写入钥匙串")
	}
	return err
}

func (k Keychain) Delete(account string) error {
	if !keychainSafe.MatchString(account) {
		return errors.New("钥匙串账户名无效")
	}
	_, status, err := k.run("delete-generic-password -s " + keychainService + " -a " + account)
	if err == nil && status != 0 && status != keychainNotFound {
		err = errors.New("无法删除钥匙串中的企业会话")
	}
	return err
}

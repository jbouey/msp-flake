package main

import (
	"context"
	"encoding/base64"
	"flag"
	"fmt"
	"io"
	"os"
	"time"
	"unicode/utf16"

	"github.com/masterzen/winrm"
)

func main() {
	host := flag.String("host", "", "WinRM host")
	user := flag.String("user", "", "Username")
	pass := flag.String("pass", "", "Password")
	cmd := flag.String("cmd", "", "PowerShell command")
	port := flag.Int("port", 5985, "WinRM port")
	timeout := flag.Int("timeout", 60, "Timeout in seconds")
	flag.Parse()

	if *host == "" || *user == "" || *pass == "" || *cmd == "" {
		fmt.Fprintf(os.Stderr, "Usage: winrm-exec -host HOST -user USER -pass PASS -cmd CMD\n")
		os.Exit(1)
	}

	endpoint := winrm.NewEndpoint(*host, *port, false, false, nil, nil, nil, time.Duration(*timeout)*time.Second)
	params := winrm.NewParameters(fmt.Sprintf("PT%dS", *timeout), "en-US", 153600)

	client, err := winrm.NewClientWithParameters(endpoint, *user, *pass, params)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create client: %v\n", err)
		os.Exit(1)
	}

	// Use CreateShell + EncodedCommand (same as daemon executor)
	shell, err := client.CreateShell()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create shell: %v\n", err)
		os.Exit(1)
	}
	defer shell.Close()

	encoded := encodePowerShell(*cmd)
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*timeout)*time.Second)
	defer cancel()

	command, err := shell.ExecuteWithContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to execute: %v\n", err)
		os.Exit(1)
	}
	defer command.Close()

	// Read stdout/stderr concurrently — sequential io.Copy deadlocks because
	// the pipe blocks until the command finishes, but Wait() is after Copy().
	done := make(chan struct{})
	go func() {
		io.Copy(os.Stderr, command.Stderr)
		close(done)
	}()
	io.Copy(os.Stdout, command.Stdout)
	<-done
	command.Wait()
	os.Exit(command.ExitCode())
}

// encodePowerShell encodes a script as UTF-16LE base64 for -EncodedCommand.
func encodePowerShell(script string) string {
	utf16Chars := utf16.Encode([]rune(script))
	bytes := make([]byte, len(utf16Chars)*2)
	for i, c := range utf16Chars {
		bytes[i*2] = byte(c)
		bytes[i*2+1] = byte(c >> 8)
	}
	return base64.StdEncoding.EncodeToString(bytes)
}

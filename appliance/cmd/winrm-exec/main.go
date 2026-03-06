package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/masterzen/winrm"
)

func main() {
	host := flag.String("host", "", "WinRM host")
	user := flag.String("user", "", "Username")
	pass := flag.String("pass", "", "Password")
	cmd := flag.String("cmd", "", "PowerShell command")
	port := flag.Int("port", 5985, "WinRM port")
	flag.Parse()

	if *host == "" || *user == "" || *pass == "" || *cmd == "" {
		fmt.Fprintf(os.Stderr, "Usage: winrm-exec -host HOST -user USER -pass PASS -cmd CMD\n")
		os.Exit(1)
	}

	endpoint := winrm.NewEndpoint(*host, *port, false, false, nil, nil, nil, 0)
	params := winrm.NewParameters("PT60S", "en-US", 153600)

	client, err := winrm.NewClientWithParameters(endpoint, *user, *pass, params)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed: %v\n", err)
		os.Exit(1)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)

	stdout, stderr, exitCode, err := client.RunPSWithContextWithString(ctx, *cmd, "")
	cancel()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed: %v\n", err)
		os.Exit(1)
	}
	if stdout != "" {
		fmt.Print(stdout)
	}
	if stderr != "" {
		fmt.Fprintf(os.Stderr, "%s", stderr)
	}
	os.Exit(exitCode)
}

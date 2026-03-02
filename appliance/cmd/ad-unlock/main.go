package main

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"io"
	"time"

	gowinrm "github.com/masterzen/winrm"
)

func main() {
	host := "192.168.88.250"
	user := "NORTHVALLEY\\adminit"
	pass := "ClinicAdmin2024!"

	endpoint := gowinrm.NewEndpoint(host, 5985, false, true, nil, nil, nil, 120*time.Second)
	params := gowinrm.NewParameters("PT120S", "en-US", 153600)
	client, err := gowinrm.NewClientWithParameters(endpoint, user, pass, params)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		return
	}

	script := `Unlock-ADAccount -Identity Administrator; $a = Get-ADUser Administrator -Properties LockedOut; @{ LockedOut = $a.LockedOut; Name = $a.Name } | ConvertTo-Json -Compress`

	utf16 := make([]byte, len(script)*2)
	for i, c := range []byte(script) {
		utf16[i*2] = c
		utf16[i*2+1] = 0
	}
	encoded := base64.StdEncoding.EncodeToString(utf16)

	shell, err := client.CreateShell()
	if err != nil {
		fmt.Printf("CreateShell error: %v\n", err)
		return
	}
	defer shell.Close()

	cmd, err := shell.Execute("powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded)
	if err != nil {
		fmt.Printf("Execute error: %v\n", err)
		return
	}
	defer cmd.Close()

	var stdoutBuf, stderrBuf bytes.Buffer
	go io.Copy(&stdoutBuf, cmd.Stdout)
	go io.Copy(&stderrBuf, cmd.Stderr)
	cmd.Wait()

	fmt.Printf("Exit: %d\nSTDOUT: %s\n", cmd.ExitCode(), stdoutBuf.String())
	if stderrBuf.Len() > 0 {
		stderr := stderrBuf.String()
		if len(stderr) > 300 {
			stderr = stderr[:300]
		}
		fmt.Printf("STDERR: %s\n", stderr)
	}
}

//go:build darwin

package checks

import (
	"context"
	"fmt"
	"strconv"
	"strings"
)

// MacDiskSpaceCheck monitors disk usage on the root volume.
//
// HIPAA Control: §164.310(d)(2)(iv) - Data Backup and Storage
type MacDiskSpaceCheck struct{}

func (c *MacDiskSpaceCheck) Name() string { return "macos_disk_space" }

func (c *MacDiskSpaceCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_disk_space",
		HIPAAControl: "164.310(d)(2)(iv)",
		Expected:     "Disk usage below 90%",
		Metadata:     make(map[string]string),
	}

	out, err := runCmd(ctx, "df", "-h", "/")
	if err != nil {
		result.Error = err
		result.Actual = "df command failed"
		return result
	}

	lines := strings.Split(out, "\n")
	if len(lines) < 2 {
		result.Actual = "Unexpected df output"
		return result
	}

	// Parse the data line (second line)
	fields := strings.Fields(lines[1])
	if len(fields) < 5 {
		result.Actual = "Cannot parse df output"
		return result
	}

	result.Metadata["filesystem"] = fields[0]
	result.Metadata["size"] = fields[1]
	result.Metadata["used"] = fields[2]
	result.Metadata["available"] = fields[3]
	result.Metadata["use_percent"] = fields[4]

	// Parse percentage (e.g., "45%")
	pctStr := strings.TrimSuffix(fields[4], "%")
	pct, err := strconv.Atoi(pctStr)
	if err != nil {
		result.Actual = "Cannot parse disk usage percentage"
		return result
	}

	if pct < 90 {
		result.Passed = true
		result.Actual = fmt.Sprintf("Disk usage: %d%%", pct)
	} else {
		result.Actual = fmt.Sprintf("Disk usage critical: %d%%", pct)
	}

	return result
}

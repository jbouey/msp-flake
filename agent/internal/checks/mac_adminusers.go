//go:build darwin

package checks

import (
	"context"
	"fmt"
	"strings"
)

// MacAdminUsersCheck audits admin user count (should be minimal).
//
// HIPAA Control: §164.312(a)(1) - Access Control
type MacAdminUsersCheck struct{}

func (c *MacAdminUsersCheck) Name() string { return "macos_admin_users" }

func (c *MacAdminUsersCheck) Run(ctx context.Context) CheckResult {
	result := CheckResult{
		CheckType:    "macos_admin_users",
		HIPAAControl: "164.312(a)(1)",
		Expected:     "3 or fewer admin users",
		Metadata:     make(map[string]string),
	}

	out, err := runCmd(ctx, "dscl", ".", "-read", "/Groups/admin", "GroupMembership")
	if err != nil {
		result.Error = err
		result.Actual = "Cannot read admin group"
		return result
	}

	// Parse: "GroupMembership: root admin user1 user2"
	members := []string{}
	parts := strings.SplitN(out, ":", 2)
	if len(parts) == 2 {
		for _, m := range strings.Fields(strings.TrimSpace(parts[1])) {
			members = append(members, m)
		}
	}

	result.Metadata["admin_users"] = strings.Join(members, ",")
	result.Metadata["admin_count"] = fmt.Sprintf("%d", len(members))

	if len(members) <= 3 {
		result.Passed = true
		result.Actual = fmt.Sprintf("%d admin users: %s", len(members), strings.Join(members, ", "))
	} else {
		result.Actual = fmt.Sprintf("%d admin users (excessive): %s", len(members), strings.Join(members, ", "))
	}

	return result
}

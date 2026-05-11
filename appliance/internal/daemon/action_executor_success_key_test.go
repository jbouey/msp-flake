package daemon

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"strings"
	"testing"
)

// TestActionExecutorEveryCaseSetsSuccessKey is the Session 219 Phase 3
// PR-3a ratchet: every `case` in `makeActionExecutor`'s switch MUST
// return a map that contains a `"success"` key (either inline as a
// literal, or via a helper like `executeRunbook` whose returned map
// already sets the key).
//
// Pre-fix the escalate case at healing_executor.go:92-98 returned
// {"escalated": true, "reason": ...} with no "success" key, which
// then exploited the (pre-PR-3a) `Success=true` default in
// l1_engine.go:328 — silently false-healing 1,137 incidents across
// 3 prod check_types (rogue_scheduled_tasks, net_unexpected_ports,
// net_host_reachability) over 90 days.
//
// PR-3a closes both the symptom (explicit success:false on escalate)
// AND the structural cause (fail-closed default in l1_engine.go).
// This ratchet prevents future regressions: any new action handler
// that omits the key will fail this test at PR time.
//
// Allowlist mechanism: cases whose body is `return d.executeRunbook(...)`
// OR `return d.executeInlineScript(...)` (or contexual variants) are
// trusted — those helpers set `success` on every return path
// (verified in source at executeRunbookCtx line ~211/223/236/252/263
// and executeInlineScriptCtx line ~454/456/462/464/472/474).
//
// Static-AST test — no daemon import / no runtime / fast.
func TestActionExecutorEveryCaseSetsSuccessKey(t *testing.T) {
	src, err := os.ReadFile("healing_executor.go")
	if err != nil {
		t.Fatalf("read healing_executor.go: %v", err)
	}

	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "healing_executor.go", src, parser.ParseComments)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}

	// Find the `makeActionExecutor` method.
	var execFn *ast.FuncDecl
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "makeActionExecutor" {
			continue
		}
		execFn = fn
		break
	}
	if execFn == nil {
		t.Fatal("makeActionExecutor not found in healing_executor.go")
	}

	// Find the switch statement inside the returned closure.
	var swStmt *ast.TypeSwitchStmt
	var swDirect *ast.SwitchStmt
	ast.Inspect(execFn, func(n ast.Node) bool {
		switch s := n.(type) {
		case *ast.TypeSwitchStmt:
			swStmt = s
			return false
		case *ast.SwitchStmt:
			swDirect = s
			return false
		}
		return true
	})

	if swDirect == nil && swStmt == nil {
		t.Fatal("switch statement not found in makeActionExecutor body")
	}

	var clauses []*ast.CaseClause
	if swDirect != nil {
		for _, stmt := range swDirect.Body.List {
			cc, ok := stmt.(*ast.CaseClause)
			if !ok {
				continue
			}
			clauses = append(clauses, cc)
		}
	}

	if len(clauses) == 0 {
		t.Fatal("no case clauses found in makeActionExecutor switch")
	}

	// Trusted helper names whose returned map already includes a
	// "success" key on every return path. Verified by source-read.
	trustedHelpers := map[string]bool{
		"executeRunbook":         true,
		"executeRunbookCtx":      true,
		"executeInlineScript":    true,
		"executeInlineScriptCtx": true,
	}

	missing := []string{}
	for _, cc := range clauses {
		caseLabel := caseLabelString(cc)
		// Walk the case body looking for a return statement.
		bodySrc := nodeSource(fset, src, cc)
		// Two pass-conditions:
		// 1. Body returns one of the trusted helpers
		// 2. Body's return map literal contains `"success":` key
		if anyTrustedHelperCalled(bodySrc, trustedHelpers) {
			continue
		}
		if strings.Contains(bodySrc, `"success":`) {
			continue
		}
		// `default` case returning `nil, fmt.Errorf(...)` is structurally
		// safe — the nil-map path is handled by l1_engine.go's
		// fail-closed default (Success=false). Error propagation
		// already prevents a false-heal claim.
		if caseLabel == "default" && strings.Contains(bodySrc, "return nil,") {
			continue
		}
		missing = append(missing, caseLabel)
	}

	if len(missing) > 0 {
		t.Errorf(
			"makeActionExecutor cases missing explicit `\"success\":` key "+
				"in returned map (and not delegating to a trusted helper):\n  %s\n\n"+
				"Session 219 Phase 3 PR-3a ratchet — every action handler "+
				"MUST set `success` explicitly. The l1_engine.go fail-closed "+
				"default treats missing key as `Success=false`, but a handler "+
				"omitting it produces a silent escalate→heal class. "+
				"Either add `\"success\": false` (or true) to the return map, "+
				"or delegate to executeRunbook/executeInlineScript.",
			strings.Join(missing, "\n  "),
		)
	}

	// Sanity: must have detected at least the known cases.
	if len(clauses) < 4 {
		t.Errorf("expected ≥4 cases in makeActionExecutor switch; got %d", len(clauses))
	}
}

// caseLabelString returns a human-readable label for the case
// clause (the literal value list, e.g. `"escalate"` or `default`).
func caseLabelString(cc *ast.CaseClause) string {
	if len(cc.List) == 0 {
		return "default"
	}
	parts := []string{}
	for _, e := range cc.List {
		if bl, ok := e.(*ast.BasicLit); ok {
			parts = append(parts, bl.Value)
		} else {
			parts = append(parts, "<?>")
		}
	}
	return strings.Join(parts, ", ")
}

// nodeSource extracts the original source text for an AST node.
func nodeSource(fset *token.FileSet, src []byte, n ast.Node) string {
	start := fset.Position(n.Pos()).Offset
	end := fset.Position(n.End()).Offset
	if start < 0 || end > len(src) || end < start {
		return ""
	}
	return string(src[start:end])
}

// anyTrustedHelperCalled returns true if the body source contains
// a call to any of the trusted helpers (which already set success).
func anyTrustedHelperCalled(body string, trusted map[string]bool) bool {
	for name := range trusted {
		// Match `d.<name>(` or bare `<name>(` to avoid substring false
		// positives like `executeRunbookSomething`.
		if strings.Contains(body, "d."+name+"(") {
			return true
		}
		if strings.Contains(body, " "+name+"(") {
			return true
		}
	}
	return false
}

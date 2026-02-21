package daemon

// runbooks_embed.go — Embeds the full runbook library (92 runbooks) into the binary.
//
// The runbooks.json file is exported from the Python agent's runbook library
// using the export script. It contains detect/remediate/verify PowerShell and
// shell scripts for all HIPAA compliance checks.
//
// To update: run the export script from packages/compliance-agent/ and rebuild.

import (
	_ "embed"
	"encoding/json"
	"log"
)

//go:embed runbooks.json
var runbooksJSON []byte

// runbookRegistry is the parsed runbook lookup table, keyed by runbook ID.
var runbookRegistry map[string]*runbookEntry

func init() {
	runbookRegistry = make(map[string]*runbookEntry)

	var raw map[string]*runbookEntry
	if err := json.Unmarshal(runbooksJSON, &raw); err != nil {
		log.Printf("[runbooks] Failed to parse embedded runbooks.json: %v", err)
		return
	}

	runbookRegistry = raw
	log.Printf("[runbooks] Loaded %d embedded runbooks (%d bytes)", len(runbookRegistry), len(runbooksJSON))
}

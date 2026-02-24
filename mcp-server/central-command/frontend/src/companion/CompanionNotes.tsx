import React, { useState } from 'react';
import { useCompanionNotes, useCreateNote, useUpdateNote, useDeleteNote } from './useCompanionApi';
import { companionColors } from './companion-tokens';

interface CompanionNotesProps {
  orgId: string;
  moduleKey: string;
  isOpen: boolean;
  onClose: () => void;
}

export const CompanionNotes: React.FC<CompanionNotesProps> = ({ orgId, moduleKey, isOpen, onClose }) => {
  const { data, isLoading } = useCompanionNotes(orgId, moduleKey);
  const createNote = useCreateNote(orgId, moduleKey);
  const updateNote = useUpdateNote();
  const deleteNote = useDeleteNote();

  const [draft, setDraft] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState('');

  const handleCreate = async () => {
    if (!draft.trim()) return;
    await createNote.mutateAsync(draft.trim());
    setDraft('');
  };

  const handleUpdate = async (noteId: string) => {
    if (!editText.trim()) return;
    await updateNote.mutateAsync({ noteId, note: editText.trim() });
    setEditingId(null);
  };

  const handleDelete = async (noteId: string) => {
    await deleteNote.mutateAsync(noteId);
  };

  if (!isOpen) return null;

  const notes = data?.notes || [];

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40" style={{ background: 'rgba(0,0,0,0.15)' }} onClick={onClose} />

      {/* Drawer */}
      <div
        className="fixed top-0 right-0 bottom-0 z-50 flex flex-col"
        style={{
          width: 380,
          background: companionColors.cardBg,
          borderLeft: `1px solid ${companionColors.divider}`,
          boxShadow: '-4px 0 24px rgba(0,0,0,0.08)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: `1px solid ${companionColors.divider}` }}>
          <h3 className="font-semibold" style={{ color: companionColors.textPrimary }}>
            Notes
          </h3>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg flex items-center justify-center hover:opacity-70"
            style={{ color: companionColors.textSecondary }}
          >
            x
          </button>
        </div>

        {/* New note input */}
        <div className="px-5 py-3" style={{ borderBottom: `1px solid ${companionColors.divider}` }}>
          <textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            placeholder="Add a note..."
            rows={3}
            className="w-full px-3 py-2 rounded-lg text-sm resize-none focus:outline-none focus:ring-2"
            style={{
              border: `1px solid ${companionColors.cardBorder}`,
              color: companionColors.textPrimary,
              // @ts-ignore
              '--tw-ring-color': companionColors.focusRing,
            }}
          />
          <button
            onClick={handleCreate}
            disabled={!draft.trim() || createNote.isPending}
            className="mt-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-opacity disabled:opacity-40"
            style={{ background: companionColors.primary, color: companionColors.textInverse }}
          >
            {createNote.isPending ? 'Saving...' : 'Add Note'}
          </button>
        </div>

        {/* Notes list */}
        <div className="flex-1 overflow-y-auto px-5 py-3">
          {isLoading ? (
            <p className="text-sm text-center py-8" style={{ color: companionColors.textTertiary }}>Loading notes...</p>
          ) : notes.length === 0 ? (
            <p className="text-sm text-center py-8" style={{ color: companionColors.textTertiary }}>No notes yet for this module.</p>
          ) : (
            <div className="space-y-3">
              {notes.map((note: any) => (
                <div
                  key={note.id}
                  className="rounded-lg p-3"
                  style={{ background: companionColors.sidebarBg, border: `1px solid ${companionColors.cardBorder}` }}
                >
                  {editingId === note.id ? (
                    <>
                      <textarea
                        value={editText}
                        onChange={e => setEditText(e.target.value)}
                        rows={3}
                        className="w-full px-2 py-1.5 rounded text-sm resize-none focus:outline-none"
                        style={{ border: `1px solid ${companionColors.cardBorder}` }}
                      />
                      <div className="flex gap-2 mt-2">
                        <button
                          onClick={() => handleUpdate(note.id)}
                          className="px-3 py-1 rounded text-xs font-medium"
                          style={{ background: companionColors.primary, color: 'white' }}
                        >
                          Save
                        </button>
                        <button
                          onClick={() => setEditingId(null)}
                          className="px-3 py-1 rounded text-xs"
                          style={{ color: companionColors.textSecondary }}
                        >
                          Cancel
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <p className="text-sm whitespace-pre-wrap" style={{ color: companionColors.textPrimary }}>
                        {note.note}
                      </p>
                      <div className="flex items-center justify-between mt-2">
                        <span className="text-xs" style={{ color: companionColors.textTertiary }}>
                          {note.companion_name} &middot; {new Date(note.created_at).toLocaleDateString()}
                        </span>
                        <div className="flex gap-2">
                          <button
                            onClick={() => { setEditingId(note.id); setEditText(note.note); }}
                            className="text-xs hover:underline"
                            style={{ color: companionColors.primary }}
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => handleDelete(note.id)}
                            className="text-xs hover:underline"
                            style={{ color: companionColors.actionNeeded }}
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
};

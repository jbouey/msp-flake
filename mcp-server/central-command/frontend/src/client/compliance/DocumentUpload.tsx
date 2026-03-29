import React, { useState, useEffect, useCallback, useRef } from 'react';
import { formatTimeAgo, formatBytes } from '../../constants';
import { csrfHeaders } from '../../utils/csrf';

interface Document {
  id: string;
  module_key: string;
  file_name: string;
  mime_type: string;
  size_bytes: number;
  description: string | null;
  uploaded_by_email: string | null;
  created_at: string;
}

interface DocumentUploadProps {
  moduleKey: string;
  apiBase?: string;
  maxDocuments?: number;
}

const ACCEPT = '.pdf,.doc,.docx';
const ALLOWED_EXTENSIONS = ['pdf', 'doc', 'docx'];
const MAX_SIZE = 25 * 1024 * 1024;
const DEFAULT_MAX_DOCS = 3;


function mimeIcon(mime: string): string {
  if (mime.includes('pdf')) return 'M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z';
  return 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z';
}

function getFileExtension(name: string): string {
  return (name.split('.').pop() || '').toLowerCase();
}

export const DocumentUpload: React.FC<DocumentUploadProps> = ({
  moduleKey,
  apiBase = '/api/client/compliance',
  maxDocuments = DEFAULT_MAX_DOCS,
}) => {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [description, setDescription] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const atLimit = documents.length >= maxDocuments;

  const fetchDocuments = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/documents?module_key=${moduleKey}`, { credentials: 'include' });
      if (res.ok) {
        const data = await res.json();
        setDocuments(data.documents || []);
      } else if (res.status !== 401) {
        setError('Failed to load documents');
      }
    } catch {
      setError('Unable to load documents — check your connection');
    } finally {
      setLoading(false);
    }
  }, [apiBase, moduleKey]);

  useEffect(() => { fetchDocuments(); }, [fetchDocuments]);

  const uploadFile = async (file: File) => {
    setError(null);
    setSuccess(null);

    // Validate file type
    const ext = getFileExtension(file.name);
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setError(`"${file.name}" is not an accepted file type. Please upload a PDF or Word document (.pdf, .docx).`);
      return;
    }

    // Validate document limit
    if (atLimit) {
      setError(`Maximum ${maxDocuments} documents allowed per section. Remove an existing document to upload a new one.`);
      return;
    }

    if (file.size > MAX_SIZE) {
      setError(`File too large (${formatBytes(file.size)}). Maximum is 25 MB.`);
      return;
    }

    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('module_key', moduleKey);
      if (description.trim()) formData.append('description', description.trim());

      const res = await fetch(`${apiBase}/documents/upload`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...csrfHeaders() },
        body: formData,
      });

      if (res.ok) {
        setDescription('');
        setSuccess(`"${file.name}" uploaded successfully`);
        setTimeout(() => setSuccess(null), 4000);
        fetchDocuments();
      } else {
        const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
        setError(err.detail || 'Upload failed');
      }
    } catch {
      setError('Upload failed — check your connection');
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) uploadFile(file);
    if (fileRef.current) fileRef.current.value = '';
  };

  const handleDownload = async (docId: string, fileName: string) => {
    try {
      const res = await fetch(`${apiBase}/documents/${docId}/download`, { credentials: 'include' });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fileName;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        setError(`Failed to download "${fileName}"`);
      }
    } catch {
      setError(`Failed to download "${fileName}" — check your connection`);
    }
  };

  const handleDelete = async (docId: string, fileName: string) => {
    if (!window.confirm(`Remove "${fileName}"? This cannot be undone.`)) return;
    setError(null);
    try {
      const res = await fetch(`${apiBase}/documents/${docId}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: { ...csrfHeaders() },
      });
      if (res.ok) {
        setDocuments(prev => prev.filter(d => d.id !== docId));
        setSuccess(`"${fileName}" removed`);
        setTimeout(() => setSuccess(null), 3000);
      } else {
        const err = await res.json().catch(() => ({ detail: 'Delete failed' }));
        setError(err.detail || `Failed to delete "${fileName}"`);
      }
    } catch {
      setError(`Failed to delete "${fileName}" — check your connection`);
    }
  };

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-base font-semibold text-slate-900">Supporting Documents</h3>
        <span className="text-xs text-slate-400">{documents.length} / {maxDocuments}</span>
      </div>

      {error && (
        <div className="mb-3 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 ml-2 text-lg leading-none">&times;</button>
        </div>
      )}

      {success && (
        <div className="mb-3 px-3 py-2 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700">
          {success}
        </div>
      )}

      {/* Upload zone */}
      {atLimit ? (
        <div className="border-2 border-dashed border-slate-200 rounded-xl p-6 text-center bg-slate-50/30">
          <p className="text-sm text-slate-400">Document limit reached ({maxDocuments} max). Remove an existing document to upload a new one.</p>
        </div>
      ) : (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
          className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors ${
            dragOver
              ? 'border-teal-400 bg-teal-50'
              : 'border-slate-200 bg-slate-50/50 hover:border-teal-300 hover:bg-teal-50/30'
          }`}
        >
          <input
            ref={fileRef}
            type="file"
            accept={ACCEPT}
            onChange={handleFileSelect}
            className="hidden"
          />
          {uploading ? (
            <div className="flex items-center justify-center gap-2">
              <div className="w-5 h-5 border-2 border-teal-500 border-t-transparent rounded-full animate-spin" />
              <span className="text-sm text-teal-600 font-medium">Uploading...</span>
            </div>
          ) : (
            <>
              <svg className="w-8 h-8 mx-auto mb-2 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              <p className="text-sm text-slate-600">
                <span className="font-medium text-teal-600">Click to upload</span> or drag and drop
              </p>
              <p className="text-xs text-slate-400 mt-1">PDF or Word documents only (max 25 MB)</p>
            </>
          )}
        </div>
      )}

      {/* Optional description */}
      {!atLimit && (
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description (optional) — added to your next upload"
          className="mt-2 w-full px-3 py-1.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-400"
        />
      )}

      {/* Document list */}
      {loading ? (
        <div className="mt-4 flex items-center gap-2 text-sm text-slate-400">
          <div className="w-4 h-4 border-2 border-slate-300 border-t-transparent rounded-full animate-spin" />
          Loading documents...
        </div>
      ) : documents.length > 0 ? (
        <div className="mt-4 space-y-2">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center gap-3 px-3 py-2.5 bg-white border border-slate-100 rounded-lg hover:border-slate-200 transition-colors group"
            >
              <svg className="w-5 h-5 text-slate-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={mimeIcon(doc.mime_type)} />
              </svg>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-900 truncate">{doc.file_name}</p>
                <p className="text-xs text-slate-400">
                  {formatBytes(doc.size_bytes)} &middot; {formatTimeAgo(doc.created_at)}
                  {doc.description && <> &middot; {doc.description}</>}
                  {doc.uploaded_by_email && <> &middot; {doc.uploaded_by_email}</>}
                </p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); handleDownload(doc.id, doc.file_name); }}
                className="p-1.5 text-slate-400 hover:text-teal-600 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                title="Download"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); handleDelete(doc.id, doc.file_name); }}
                className="p-1.5 text-slate-400 hover:text-red-500 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                title="Remove"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-3 text-xs text-slate-400">No documents uploaded yet.</p>
      )}
    </div>
  );
};

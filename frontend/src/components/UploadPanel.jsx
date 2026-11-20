import { useRef, useState } from 'react';

const SUPPORTED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.webp', '.bmp'];

function formatSize(bytes) {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function UploadPanel({ file, onFileSelected, onClearFile, form, updateForm, onSubmit, submitting }) {
  const fileInputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  function handleFiles(files) {
    if (!files || !files.length) return;
    onFileSelected(files[0]);
  }

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    handleFiles(e.dataTransfer.files);
  }

  const isPdf = file && file.name.toLowerCase().endsWith('.pdf');

  return (
    <div className="panel">
      <div className="form-group">
        <label>Upload Document (PDF / Image)</label>

        {!file ? (
          <label
            className={`upload-zone ${dragging ? 'dragging' : ''}`}
            style={{ cursor: 'pointer', display: 'block' }}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <i className="fa-solid fa-cloud-arrow-up" />
            <span>Drag &amp; Drop or Click to Browse</span>
            <p>PDF, PNG, JPG, WEBP, BMP</p>
            <input
              ref={fileInputRef}
              type="file"
              accept={SUPPORTED_EXTENSIONS.join(',')}
              onChange={(e) => handleFiles(e.target.files)}
              data-testid="file-input"
            />
          </label>
        ) : (
          <div className="upload-zone file-ready">
            <i className="fa-solid fa-file-circle-check" style={{ fontSize: '1.6rem', color: 'var(--success)', display: 'block', marginBottom: 6 }} />
            <span className="dz-filename">{file.name}</span>
            <p className="dz-meta">{file.name.split('.').pop().toUpperCase()} · {formatSize(file.size)}</p>
            <div className="dz-actions">
              <button type="button" className="btn btn-sm btn-outline" onClick={() => fileInputRef.current?.click()}>
                <i className="fa-solid fa-folder-open" /> Change
              </button>
              <button type="button" className="btn btn-sm btn-danger" onClick={onClearFile}>
                <i className="fa-solid fa-xmark" /> Clear
              </button>
              <input
                ref={fileInputRef}
                type="file"
                style={{ display: 'none' }}
                accept={SUPPORTED_EXTENSIONS.join(',')}
                onChange={(e) => handleFiles(e.target.files)}
              />
            </div>
          </div>
        )}

        {isPdf && (
          <div id="pageRangeGroup">
            <p className="page-count-hint">PDF selected — enter a page range or leave blank to extract all pages</p>
            <input
              type="text"
              placeholder="Pages to extract: e.g. 1-10, 25, 50-100 (leave blank = all)"
              value={form.pageRange}
              onChange={(e) => updateForm({ pageRange: e.target.value })}
            />
          </div>
        )}
      </div>

      <button className="btn btn-primary" disabled={!file || submitting} onClick={onSubmit} data-testid="submit-btn">
        {submitting ? (<><div className="spinner" /> Submitting...</>) : (<><i className="fa-solid fa-play" /> Start OCR Extraction</>)}
      </button>
    </div>
  );
}

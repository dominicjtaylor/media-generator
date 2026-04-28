import React, { useState, useEffect, useRef } from 'react'

export default function ImagePicker({ onSelect, onBack }) {
  const [images,        setImages]        = useState([])
  const [loading,       setLoading]       = useState(true)
  const [selected,      setSelected]      = useState(null)
  const [uploadedImage, setUploadedImage] = useState(null)  // {filename, url}
  const [uploadPreview, setUploadPreview] = useState(null)  // object URL
  const [uploading,     setUploading]     = useState(false)
  const previewRef = useRef(null)

  useEffect(() => {
    fetch('/api/images')
      .then(r => r.json())
      .then(data => { setImages(data.images || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    return () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    }
  }, [])

  const handleUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return

    // Show preview immediately
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    const preview = URL.createObjectURL(file)
    previewRef.current = preview
    setUploadPreview(preview)
    setUploading(true)

    try {
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch('/upload-cover-image', { method: 'POST', body: fd })
      if (!res.ok) throw new Error('Upload failed')
      const data = await res.json()
      setUploadedImage({ filename: data.filename, url: data.url, thumbnail_url: data.url })
      setSelected(null)  // uploaded takes priority; clear library selection
    } catch {
      setUploadPreview(null)
      setUploadedImage(null)
    } finally {
      setUploading(false)
    }
  }

  const clearUpload = () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    previewRef.current = null
    setUploadPreview(null)
    setUploadedImage(null)
  }

  const activeImage = uploadedImage || selected
  const canContinue = activeImage !== null && !uploading

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="font-semibold text-base">Pick your image</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
          Choose the image for slide 1
        </p>
      </div>

      {/* 1 — Upload section (priority) */}
      <div className="space-y-2">
        <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Upload image</p>

        {uploadPreview ? (
          <div className="flex items-center gap-3 p-3 border rounded-xl border-accent/40 bg-accent/5">
            <img src={uploadPreview} className="w-14 h-14 rounded-lg object-cover border border-accent/30" alt="uploaded" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-accent">Uploaded image</p>
              {uploading && <p className="text-xs text-gray-400">Uploading…</p>}
            </div>
            <button
              type="button"
              onClick={clearUpload}
              className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
            >
              Remove
            </button>
          </div>
        ) : (
          <label className="flex items-center gap-3 p-4 rounded-xl border-2 border-dashed border-gray-300 dark:border-gray-700 hover:border-accent transition-colors cursor-pointer">
            <input type="file" className="sr-only" accept="image/*" onChange={handleUpload} />
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-gray-400 shrink-0">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            <span className="text-sm text-gray-500 dark:text-gray-400">Click to upload your own image</span>
          </label>
        )}
      </div>

      {/* 2 — Library selection */}
      <div className="space-y-2">
        <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
          Or select from library
        </p>

        {loading ? (
          <div className="text-sm text-gray-400 dark:text-gray-500">Loading images…</div>
        ) : images.length === 0 ? (
          <div className="text-sm text-gray-400 dark:text-gray-500">No images available.</div>
        ) : (
          <div className={`grid grid-cols-3 gap-3 ${uploadedImage ? 'opacity-40 pointer-events-none' : ''}`}>
            {images.map((img) => (
              <button
                key={img.filename}
                type="button"
                onClick={() => setSelected(img)}
                className={`
                  relative h-40 rounded-xl overflow-hidden border-2 transition-all duration-150
                  ${!uploadedImage && selected?.filename === img.filename
                    ? 'border-accent ring-2 ring-accent/20'
                    : 'border-transparent hover:border-gray-300 dark:hover:border-gray-600'
                  }
                `}
              >
                <img
                  src={img.url}
                  alt={img.filename.replace(/\.[^.]+$/, '')}
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
              </button>
            ))}
          </div>
        )}

        {!uploadedImage && selected && (
          <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
            {selected.filename.replace(/\.[^.]+$/, '')}
          </p>
        )}
      </div>

      <div className="flex gap-3">
        <button
          type="button"
          onClick={onBack}
          className="px-4 py-3 rounded-xl text-sm font-medium text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          Back
        </button>
        <button
          type="button"
          onClick={() => canContinue && onSelect(activeImage)}
          disabled={!canContinue}
          className="
            flex-1 flex items-center justify-center gap-2
            bg-accent hover:bg-accent-hover
            disabled:opacity-40 disabled:cursor-not-allowed
            text-white font-semibold text-sm
            px-5 py-3 rounded-xl
            transition-all active:scale-[0.98]
            shadow-sm
          "
        >
          Use this image
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
        </button>
      </div>
    </div>
  )
}

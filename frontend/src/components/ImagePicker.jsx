import React, { useState, useEffect } from 'react'

export default function ImagePicker({ onSelect, onBack }) {
  const [images,   setImages]   = useState([])
  const [loading,  setLoading]  = useState(true)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    fetch('/api/images')
      .then(r => r.json())
      .then(data => { setImages(data.images || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const canContinue = selected !== null

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="font-semibold text-base">Pick your image</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
          Choose the image for slide 1
        </p>
      </div>

      {loading ? (
        <div className="text-sm text-gray-400 dark:text-gray-500">Loading images…</div>
      ) : images.length === 0 ? (
        <div className="text-sm text-gray-400 dark:text-gray-500">No images available.</div>
      ) : (
        <div className="grid grid-cols-3 gap-2 max-h-72 overflow-y-auto pr-1">
          {images.map((img) => (
            <button
              key={img.filename}
              type="button"
              onClick={() => setSelected(img)}
              className={`
                relative aspect-square rounded-xl overflow-hidden border-2 transition-all duration-150
                ${selected?.filename === img.filename
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

      {selected && (
        <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
          {selected.filename.replace(/\.[^.]+$/, '')}
        </p>
      )}

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
          onClick={() => canContinue && onSelect(selected)}
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

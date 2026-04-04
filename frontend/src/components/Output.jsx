import React, { useState } from 'react'

// ---------------------------------------------------------------------------
// Download helpers
// ---------------------------------------------------------------------------
function downloadCSV(csvContent, topic) {
  const slug = topic?.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'carousel'
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `${slug}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

async function downloadImage(url, index) {
  const res  = await fetch(url)
  const blob = await res.blob()
  const a    = document.createElement('a')
  a.href     = URL.createObjectURL(blob)
  a.download = `carousel-slide-${index + 1}.png`
  a.click()
  URL.revokeObjectURL(a.href)
}

async function copyText(text) {
  await navigator.clipboard.writeText(text)
}

// ---------------------------------------------------------------------------
// Slide type label
// ---------------------------------------------------------------------------
function slideLabel(index, total) {
  if (index === 0)         return { label: 'Hook',    color: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300' }
  if (index === total - 1) return { label: 'CTA',     color: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
  return                          { label: 'Content', color: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400' }
}

// ---------------------------------------------------------------------------
// Single animated status message (replaces skeleton during SSE streaming)
// ---------------------------------------------------------------------------
function StatusMessage({ message }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4 animate-fade-in">
      <svg className="animate-spin h-6 w-6 text-accent" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
      </svg>
      <p
        key={message}
        className="text-sm text-gray-500 dark:text-gray-400 animate-fade-in"
      >
        {message || 'Starting…'}
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single image card with download button
// ---------------------------------------------------------------------------
function ImageCard({ url, index }) {
  const [downloading, setDownloading] = useState(false)

  const handleDownload = async (e) => {
    e.stopPropagation()
    if (downloading) return
    setDownloading(true)
    try {
      await downloadImage(url, index)
    } finally {
      setTimeout(() => setDownloading(false), 1500)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Clicking the image opens it full-size in a new tab */}
      <button
        type="button"
        onClick={() => window.open(url, '_blank')}
        className="block w-full cursor-zoom-in rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow duration-150 border border-gray-100 dark:border-gray-800"
      >
        <img
          src={url}
          alt={`Carousel slide ${index + 1}`}
          className="w-full h-auto block"
          loading="lazy"
        />
      </button>

      {/* Per-image download button */}
      <button
        type="button"
        onClick={handleDownload}
        disabled={downloading}
        className="
          w-full flex items-center justify-center gap-2
          bg-gray-900 dark:bg-gray-100 hover:bg-gray-700 dark:hover:bg-gray-300
          disabled:opacity-50 disabled:cursor-not-allowed
          text-white dark:text-gray-900 text-xs font-semibold
          py-2.5 rounded-xl
          transition-colors duration-150
        "
      >
        {downloading ? (
          <>
            <svg className="animate-spin h-3.5 w-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
            Saving…
          </>
        ) : (
          <>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            Download
          </>
        )}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Image gallery (shown when Contentdrips returns direct image URLs)
// ---------------------------------------------------------------------------
function ImagesGallery({ images, onToast }) {
  return (
    <div className="space-y-4 animate-slide-up">
      <div className="flex items-center gap-2.5">
        <span className="w-7 h-7 rounded-full bg-emerald-100 dark:bg-emerald-900 flex items-center justify-center shrink-0">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
        </span>
        <div>
          <p className="font-semibold text-sm text-emerald-900 dark:text-emerald-100">Carousel rendered!</p>
          <p className="text-xs text-emerald-600 dark:text-emerald-400">
            Tap an image to view full-size · Download each slide below.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {images.map((url, i) => (
          <ImageCard key={url} url={url} index={i} />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Slides preview (always shown when slides are present)
// ---------------------------------------------------------------------------
function SlidesPreview({ slides, csv, onToast }) {
  const topic = slides[0]?.heading ?? ''
  return (
    <div className="space-y-5 animate-slide-up">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-sm">Slide preview</h2>
          <p className="text-xs text-gray-400 mt-0.5">{slides.length} slides</p>
        </div>
        {csv && (
          <div className="flex items-center gap-2">
            <button
              onClick={async () => { await copyText(csv); onToast('Copied to clipboard!') }}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
              Copy CSV
            </button>
          </div>
        )}
      </div>

      <div className="space-y-3">
        {slides.map((slide, i) => {
          const { label, color } = slideLabel(i, slides.length)
          return (
            <div
              key={i}
              className="rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 space-y-2.5 shadow-sm hover:shadow-md hover:border-gray-300 dark:hover:border-gray-700 transition-all duration-150"
            >
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-gray-400 dark:text-gray-600 tabular-nums w-4">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide ${color}`}>
                  {label}
                </span>
              </div>
              <p className="font-semibold text-base leading-snug">{slide.heading}</p>
              <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">{slide.description}</p>
            </div>
          )
        })}
      </div>

      {/* CSV download — shown only in fallback mode (no images) */}
      {csv && (
        <button
          onClick={() => { downloadCSV(csv, topic); onToast('CSV downloaded!') }}
          className="w-full flex items-center justify-center gap-2.5 bg-accent hover:bg-accent-hover text-white font-semibold text-sm px-5 py-3.5 rounded-xl transition-all active:scale-[0.98] shadow-sm hover:shadow-md"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          Download CSV
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Output component
// ---------------------------------------------------------------------------
export default function Output({ status, data, errorMsg, stepMessage, onToast }) {
  if (status === 'loading') return <StatusMessage message={stepMessage} />

  if (status === 'error') {
    return (
      <div className="rounded-xl border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-600 dark:text-red-400 animate-slide-up">
        <div className="flex items-start gap-3">
          <svg className="mt-0.5 shrink-0" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          <span>{errorMsg || 'Something went wrong. Please try again.'}</span>
        </div>
      </div>
    )
  }

  if (status !== 'done' || !data) return null

  const { images = [], slides = [], csv } = data

  return (
    <div className="space-y-8">
      {/* Rendered image gallery (Contentdrips mode) */}
      {images.length > 0 && (
        <ImagesGallery images={images} onToast={onToast} />
      )}

      {/* Slide cards preview (always shown when slides present) */}
      {slides.length > 0 && (
        <SlidesPreview
          slides={slides}
          csv={images.length > 0 ? null : csv}  // hide CSV download when images available
          onToast={onToast}
        />
      )}
    </div>
  )
}

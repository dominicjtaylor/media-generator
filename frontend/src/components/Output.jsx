import React, { useState, useEffect, useCallback, useRef } from 'react'

// ---------------------------------------------------------------------------
// Device detection
// ---------------------------------------------------------------------------
function useIsMobile() {
  const [isMobile, setIsMobile] = useState(false)
  useEffect(() => {
    setIsMobile(/iPhone|iPad|iPod|Android/i.test(navigator.userAgent))
  }, [])
  return isMobile
}

// Can this browser share files natively (iOS Safari, Android Chrome)?
function canShareFiles() {
  if (!navigator.share || !navigator.canShare) return false
  try {
    return navigator.canShare({ files: [new File([], 'test.png', { type: 'image/png' })] })
  } catch {
    return false
  }
}

// ---------------------------------------------------------------------------
// Download helpers
// ---------------------------------------------------------------------------
async function fetchBlob(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Failed to fetch image (${res.status})`)
  return res.blob()
}

// Mobile: open native share sheet → user taps "Save Image" → goes to Photos
async function shareImage(url, index) {
  const blob = await fetchBlob(url)
  const file = new File([blob], `carousel-slide-${index + 1}.png`, { type: 'image/png' })
  await navigator.share({ files: [file], title: `Slide ${index + 1}` })
}

// Desktop: trigger browser download
async function downloadImage(url, index) {
  const blob = await fetchBlob(url)
  const a    = document.createElement('a')
  a.href     = URL.createObjectURL(blob)
  a.download = `carousel-slide-${index + 1}.png`
  a.click()
  URL.revokeObjectURL(a.href)
}

// Desktop: download all slides sequentially
async function downloadAll(images) {
  for (let i = 0; i < images.length; i++) {
    await downloadImage(images[i], i)
    // Small gap prevents browsers from blocking rapid-fire downloads
    if (i < images.length - 1) await new Promise(r => setTimeout(r, 300))
  }
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
// Fullscreen lightbox (mobile tap-to-view)
// ---------------------------------------------------------------------------
function Lightbox({ url, index, total, onClose, onPrev, onNext }) {
  // Close on Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  // Prevent body scroll while open
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  return (
    <div
      className="fixed inset-0 z-50 bg-black/95 flex flex-col items-center justify-center"
      onClick={onClose}
    >
      {/* Close */}
      <button
        className="absolute top-4 right-4 text-white/70 hover:text-white p-2"
        onClick={onClose}
        aria-label="Close"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>

      {/* Slide counter */}
      <p className="absolute top-5 left-1/2 -translate-x-1/2 text-white/50 text-xs tabular-nums">
        {index + 1} / {total}
      </p>

      {/* Image */}
      <img
        src={url}
        alt={`Slide ${index + 1}`}
        className="max-h-[80vh] max-w-[92vw] object-contain rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      />

      {/* Prev / Next */}
      {onPrev && (
        <button
          className="absolute left-3 top-1/2 -translate-y-1/2 text-white/60 hover:text-white p-3"
          onClick={(e) => { e.stopPropagation(); onPrev() }}
          aria-label="Previous slide"
        >
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
        </button>
      )}
      {onNext && (
        <button
          className="absolute right-3 top-1/2 -translate-y-1/2 text-white/60 hover:text-white p-3"
          onClick={(e) => { e.stopPropagation(); onNext() }}
          aria-label="Next slide"
        >
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
        </button>
      )}

      {/* Hint */}
      <p className="absolute bottom-6 left-1/2 -translate-x-1/2 text-white/40 text-xs text-center px-4">
        Tap outside to close
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single image card
// ---------------------------------------------------------------------------
function ImageCard({ url, index, isMobile, onOpenLightbox }) {
  const [busy, setBusy]           = useState(false)
  const [saved, setSaved]         = useState(false)
  const useShare                  = isMobile && canShareFiles()

  const handleSave = async (e) => {
    e.stopPropagation()
    if (busy) return
    setBusy(true)
    try {
      if (useShare) {
        await shareImage(url, index)
      } else {
        await downloadImage(url, index)
      }
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      // User cancelled the share sheet — not an error
      if (err?.name !== 'AbortError') console.error(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Image — tap opens lightbox */}
      <button
        type="button"
        onClick={() => onOpenLightbox(index)}
        className="block w-full cursor-zoom-in rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow duration-150 border border-gray-100 dark:border-gray-800 relative group"
        aria-label={`View slide ${index + 1} fullscreen`}
      >
        <img
          src={url}
          alt={`Carousel slide ${index + 1}`}
          className="w-full h-auto block"
          loading="lazy"
        />
        {/* Hover overlay hint (desktop) */}
        {!isMobile && (
          <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors duration-150 flex items-center justify-center">
            <span className="opacity-0 group-hover:opacity-100 transition-opacity duration-150 bg-black/60 text-white text-xs px-2.5 py-1 rounded-full">
              View fullscreen
            </span>
          </div>
        )}
      </button>

      {/* Mobile hint label */}
      {isMobile && (
        <p className="text-[11px] text-center text-gray-400 dark:text-gray-500 -mt-1">
          Tap image to preview
        </p>
      )}

      {/* Save / Download button */}
      <button
        type="button"
        onClick={handleSave}
        disabled={busy}
        className="
          w-full flex items-center justify-center gap-2
          bg-gray-900 dark:bg-gray-100 hover:bg-gray-700 dark:hover:bg-gray-300
          disabled:opacity-50 disabled:cursor-not-allowed
          text-white dark:text-gray-900 text-xs font-semibold
          py-2.5 rounded-xl
          transition-colors duration-150
        "
      >
        {busy ? (
          <>
            <svg className="animate-spin h-3.5 w-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
            {isMobile ? 'Opening…' : 'Saving…'}
          </>
        ) : saved ? (
          <>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            Saved!
          </>
        ) : (
          <>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            {isMobile ? 'Save to Photos' : 'Download'}
          </>
        )}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single animated status message
// ---------------------------------------------------------------------------
function StatusMessage({ message }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4 animate-fade-in">
      <svg className="animate-spin h-6 w-6 text-accent" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
      </svg>
      <p key={message} className="text-sm text-gray-500 dark:text-gray-400 animate-fade-in">
        {message || 'Starting…'}
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Image gallery
// ---------------------------------------------------------------------------
function ImagesGallery({ images, isMobile, onToast }) {
  const [lightboxIndex, setLightboxIndex] = useState(null)
  const [dlAll, setDlAll]                 = useState(false)

  const openLightbox  = useCallback((i) => setLightboxIndex(i), [])
  const closeLightbox = useCallback(() => setLightboxIndex(null), [])
  const prevSlide     = lightboxIndex > 0 ? () => setLightboxIndex(i => i - 1) : null
  const nextSlide     = lightboxIndex < images.length - 1 ? () => setLightboxIndex(i => i + 1) : null

  const handleDownloadAll = async () => {
    if (dlAll) return
    setDlAll(true)
    try {
      await downloadAll(images)
      onToast('All slides downloaded!')
    } catch (err) {
      console.error(err)
    } finally {
      setDlAll(false)
    }
  }

  return (
    <>
      {lightboxIndex !== null && (
        <Lightbox
          url={images[lightboxIndex]}
          index={lightboxIndex}
          total={images.length}
          onClose={closeLightbox}
          onPrev={prevSlide}
          onNext={nextSlide}
        />
      )}

      <div className="space-y-4 animate-slide-up">
        {/* Header */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="w-7 h-7 rounded-full bg-emerald-100 dark:bg-emerald-900 flex items-center justify-center shrink-0">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            </span>
            <div>
              <p className="font-semibold text-sm text-emerald-900 dark:text-emerald-100">Carousel rendered!</p>
              <p className="text-xs text-emerald-600 dark:text-emerald-400">
                {isMobile
                  ? 'Tap a slide to preview · Save to Photos below'
                  : 'Tap to preview · Download individually or all at once'}
              </p>
            </div>
          </div>

          {/* Desktop: Download All button */}
          {!isMobile && (
            <button
              type="button"
              onClick={handleDownloadAll}
              disabled={dlAll}
              className="
                shrink-0 flex items-center gap-1.5
                bg-gray-900 dark:bg-gray-100 hover:bg-gray-700 dark:hover:bg-gray-300
                disabled:opacity-50 disabled:cursor-not-allowed
                text-white dark:text-gray-900 text-xs font-semibold
                px-3.5 py-2 rounded-xl
                transition-colors duration-150 whitespace-nowrap
              "
            >
              {dlAll ? (
                <>
                  <svg className="animate-spin h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                  </svg>
                  Downloading…
                </>
              ) : (
                <>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                  </svg>
                  Download All
                </>
              )}
            </button>
          )}
        </div>

        {/* Mobile save hint */}
        {isMobile && (
          <div className="flex items-center gap-2 bg-blue-50 dark:bg-blue-950/30 border border-blue-100 dark:border-blue-900 rounded-xl px-4 py-3">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
              <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            <p className="text-xs text-blue-700 dark:text-blue-300">
              Tap <strong>Save to Photos</strong> under each slide to add it to your camera roll.
            </p>
          </div>
        )}

        {/* Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {images.map((url, i) => (
            <ImageCard
              key={url}
              url={url}
              index={i}
              isMobile={isMobile}
              onOpenLightbox={openLightbox}
            />
          ))}
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Caption card
// ---------------------------------------------------------------------------
function CaptionCard({ caption, onToast }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(caption)
      setCopied(true)
      onToast('Caption copied!')
      setTimeout(() => setCopied(false), 2000)
    } catch {
      onToast('Copy failed — select and copy manually')
    }
  }

  // Split into lines, separating hashtags (lines starting with #) from body
  const lines     = caption.split('\n').filter(l => l.trim())
  const hashLines = lines.filter(l => l.trim().startsWith('#'))
  const bodyLines = lines.filter(l => !l.trim().startsWith('#'))

  return (
    <div className="space-y-3 animate-slide-up">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-sm">Caption</h2>
          <p className="text-xs text-gray-400 mt-0.5">Ready to paste into Instagram</p>
        </div>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          {copied ? (
            <>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12"/>
              </svg>
              Copied!
            </>
          ) : (
            <>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
              Copy
            </>
          )}
        </button>
      </div>

      <div className="rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 shadow-sm space-y-3">
        {/* Body lines */}
        <div className="space-y-2">
          {bodyLines.map((line, i) => (
            <p
              key={i}
              className={
                i === 0
                  ? 'font-semibold text-sm leading-snug'                          // hook line
                  : line.toLowerCase().includes('@claudeinsights')
                    ? 'text-xs text-accent font-medium'                           // CTA line
                    : 'text-sm text-gray-600 dark:text-gray-400 leading-relaxed'  // body lines
              }
            >
              {line}
            </p>
          ))}
        </div>

        {/* Hashtags */}
        {hashLines.length > 0 && (
          <p className="text-xs text-blue-500 dark:text-blue-400 leading-relaxed pt-1 border-t border-gray-100 dark:border-gray-800">
            {hashLines.join(' ')}
          </p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Slides preview
// ---------------------------------------------------------------------------
function SlidesPreview({ slides }) {
  return (
    <div className="space-y-5 animate-slide-up">
      <div>
        <h2 className="font-semibold text-sm">Slide preview</h2>
        <p className="text-xs text-gray-400 mt-0.5">{slides.length} slides</p>
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
              {slide.description && (
                <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">{slide.description}</p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Output component
// ---------------------------------------------------------------------------
export default function Output({ status, data, errorMsg, stepMessage, onToast }) {
  const isMobile = useIsMobile()

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

  const { images = [], slides = [], caption } = data

  return (
    <div className="space-y-8">
      {images.length > 0 && (
        <ImagesGallery
          images={images}
          isMobile={isMobile}
          onToast={onToast}
        />
      )}
      {caption && (
        <CaptionCard caption={caption} onToast={onToast} />
      )}
      {slides.length > 0 && (
        <SlidesPreview slides={slides} />
      )}
    </div>
  )
}

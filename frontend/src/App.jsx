import React, { useState, useEffect, useCallback, useRef } from 'react'
import Form from './components/Form.jsx'
import HookPicker from './components/HookPicker.jsx'
import ImagePicker from './components/ImagePicker.jsx'
import SlideReview from './components/SlideReview.jsx'
import Output from './components/Output.jsx'
import Toast from './components/Toast.jsx'

// Stage flow:
// idle → hooks_loading → hook_selection → image_selection
//      → slides_loading → qc_loading → review
//      → rendering → done
// Any stage → error

function LoadingPane({ message }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4 animate-fade-in">
      <svg className="animate-spin h-6 w-6 text-accent" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
      </svg>
      <p className="text-sm text-gray-500 dark:text-gray-400">{message || 'Working…'}</p>
    </div>
  )
}

async function readSse(res, onEvent) {
  const reader  = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer    = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try { onEvent(JSON.parse(line.slice(6))) } catch { /* ignore malformed */ }
    }
  }
}

export default function App() {
  const [dark, setDark]       = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  const [status, setStatus]   = useState('idle')
  const [stepMsg, setStepMsg] = useState('')
  const [errorMsg, setError]  = useState('')
  const [toast, setToast]     = useState(null)

  // Pipeline data
  const [topic,          setTopic]          = useState('')
  const [numSlides,      setNumSlides]      = useState(5)
  const [hooks,          setHooks]          = useState([])
  const [selectedHook,   setSelectedHook]   = useState(null)
  const [selectedImage,  setSelectedImage]  = useState(null)
  const [slides,         setSlides]         = useState([])
  const [caption,        setCaption]        = useState('')
  const [carouselStyle,  setCarouselStyle]  = useState('text_only')
  const [flags,          setFlags]          = useState([])
  const [images,         setImages]         = useState([])

  // Keep slides in a ref so QC callbacks always read latest value
  const slidesRef = useRef(slides)
  useEffect(() => { slidesRef.current = slides }, [slides])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])

  const showToast = useCallback((message, type = 'success') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3500)
  }, [])

  const goError = useCallback((msg) => {
    setError(msg)
    setStatus('error')
    showToast(msg, 'error')
  }, [showToast])

  const reset = useCallback(() => {
    setStatus('idle')
    setError('')
    setStepMsg('')
    setHooks([])
    setSelectedHook(null)
    setSelectedImage(null)
    setSlides([])
    setCaption('')
    setFlags([])
    setImages([])
  }, [])

  // ── Stage 1: form → hooks ─────────────────────────────────────────────────
  const handleGenerate = useCallback(async ({ topic: t, slides: n }) => {
    const trimmed = t.trim()
    setTopic(trimmed)
    setNumSlides(n)
    setStatus('hooks_loading')
    setStepMsg('Generating hook options…')
    try {
      const res = await fetch('/hooks', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ topic: trimmed, num_slides: n }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (${res.status})`)
      }
      const data = await res.json()
      setHooks(data.hooks || [])
      setStatus('hook_selection')
    } catch (err) {
      goError(err.message || 'Failed to generate hooks.')
    }
  }, [goError])

  // ── Stage 2a: hook selected → image selection ────────────────────────────
  const handleHookSelect = useCallback((hook) => {
    setSelectedHook(hook)
    setStatus('image_selection')
  }, [])

  // ── Stage 2b: image selected → slides (SSE) → auto-QC ───────────────────
  const runQc = useCallback(async (slidesToCheck) => {
    setStatus('qc_loading')
    setStepMsg('Running quality check…')
    try {
      const res = await fetch('/qc', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ topic, slides: slidesToCheck }),
      })
      const data = res.ok ? await res.json() : { flags: [] }
      setFlags(data.flags || [])
    } catch {
      setFlags([])
    }
    setStatus('review')
  }, [topic])

  const handleImageConfirm = useCallback(async (image) => {
    setSelectedImage(image)
    setStatus('slides_loading')
    setStepMsg('Generating slides…')
    try {
      const res = await fetch('/slides', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          topic,
          hook:           selectedHook.hook,
          num_slides:     numSlides,
          image_filename: image.filename,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (${res.status})`)
      }
      let gotComplete = false
      let gotError = false
      await readSse(res, (event) => {
        if (event.step === 'complete') {
          gotComplete = true
          setSlides(event.slides || [])
          setCaption(event.caption || '')
          setCarouselStyle(event.style || 'text_only')
          runQc(event.slides || [])
        } else if (event.step === 'error') {
          gotError = true
          goError(event.message || 'Slide generation failed.')
        } else {
          setStepMsg(event.message || '')
        }
      })
      if (!gotComplete && !gotError) goError('Slide generation did not complete.')
    } catch (err) {
      goError(err.message || 'Slide generation failed.')
    }
  }, [topic, numSlides, selectedHook, goError, runQc])

  // ── Per-slide regenerate ──────────────────────────────────────────────────
  const handleRegenerate = useCallback(async (index, flag) => {
    const f = flag || {}
    try {
      const res = await fetch('/regenerate', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          topic,
          slide_index: index,
          hook:        selectedHook?.hook || '',
          slides:      slidesRef.current,
          issue:       f.issue       || '',
          suggestion:  f.suggestion  || '',
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Regeneration failed (${res.status})`)
      }
      const data = await res.json()
      setSlides(prev => prev.map((s, i) => i === index ? data.slide : s))
      setFlags(prev => prev.filter(fl => (fl.slide_number - 1) !== index))
      showToast('Slide regenerated!')
    } catch (err) {
      showToast(err.message || 'Regeneration failed.', 'error')
    }
  }, [topic, selectedHook, showToast])

  // Apply the QC-suggested replacement directly (no API call)
  const handleApplyFix = useCallback((index, flag) => {
    setSlides(prev => prev.map((s, i) => i === index ? {
      ...s,
      heading:     flag.replacement_heading || s.heading,
      description: flag.replacement_body    || s.description,
    } : s))
    setFlags(prev => prev.filter(f => (f.slide_number - 1) !== index))
    showToast('Fix applied!')
  }, [showToast])

  const handleDismissFlag = useCallback((slideIndex) => {
    setFlags(prev => prev.filter(f => (f.slide_number - 1) !== slideIndex))
  }, [])

  const handleRerunQc = useCallback(() => runQc(slidesRef.current), [runQc])

  // ── Stage 3: render ───────────────────────────────────────────────────────
  const handleRender = useCallback(async () => {
    setStatus('rendering')
    setStepMsg('Rendering slides…')
    try {
      const res = await fetch('/render', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ topic, slides: slidesRef.current, style: carouselStyle, image_filename: selectedImage?.filename || null }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Render failed (${res.status})`)
      }
      let gotComplete = false
      let gotError = false
      await readSse(res, (event) => {
        if (event.step === 'complete') {
          gotComplete = true
          setImages(event.images || [])
          setStatus('done')
          showToast('Carousel rendered!')
        } else if (event.step === 'error') {
          gotError = true
          goError(event.message || 'Rendering failed.')
        } else {
          setStepMsg(event.message || '')
        }
      })
      if (!gotComplete && !gotError) goError('Render did not complete.')
    } catch (err) {
      goError(err.message || 'Rendering failed.')
    }
  }, [topic, carouselStyle, selectedImage, goError, showToast, status])

  const LOADING_STAGES = new Set(['hooks_loading', 'slides_loading', 'qc_loading', 'rendering'])

  return (
    <div className="min-h-screen flex flex-col">

      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950">
        <div className="max-w-2xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="w-6 h-6 rounded-md bg-accent flex items-center justify-center">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <rect x="1" y="1" width="5" height="5" rx="1" fill="white"/>
                <rect x="8" y="1" width="5" height="5" rx="1" fill="white" fillOpacity=".6"/>
                <rect x="1" y="8" width="5" height="5" rx="1" fill="white" fillOpacity=".6"/>
                <rect x="8" y="8" width="5" height="5" rx="1" fill="white" fillOpacity=".3"/>
              </svg>
            </span>
            <span className="font-semibold text-sm tracking-tight">Carousel</span>
          </div>

          <button
            onClick={() => setDark(d => !d)}
            aria-label="Toggle dark mode"
            className="w-8 h-8 rounded-lg flex items-center justify-center text-gray-500 hover:text-gray-900 hover:bg-gray-100 dark:hover:text-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            {dark ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/>
                <line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/>
                <line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
              </svg>
            )}
          </button>
        </div>
      </header>

      {/* ── Main ───────────────────────────────────────────────── */}
      <main className="flex-1 max-w-2xl mx-auto w-full px-4 py-12 flex flex-col gap-10">

        {/* Hero */}
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">Generate your carousel</h1>
          <p className="text-gray-500 dark:text-gray-400 text-sm">
            Turn any topic into a polished Instagram carousel in seconds.
          </p>
        </div>

        {/* ── idle: show form ── */}
        {status === 'idle' && (
          <Form onGenerate={handleGenerate} loading={false} onReset={null} />
        )}

        {/* ── loading spinner ── */}
        {LOADING_STAGES.has(status) && <LoadingPane message={stepMsg} />}

        {/* ── hook selection ── */}
        {status === 'hook_selection' && (
          <HookPicker
            hooks={hooks}
            topic={topic}
            onSelect={handleHookSelect}
            onBack={reset}
          />
        )}

        {/* ── image selection ── */}
        {status === 'image_selection' && (
          <ImagePicker
            onSelect={handleImageConfirm}
            onBack={() => setStatus('hook_selection')}
          />
        )}

        {/* ── slide review ── */}
        {status === 'review' && (
          <SlideReview
            slides={slides}
            flags={flags}
            caption={caption}
            onRegenerate={handleRegenerate}
            onApplyFix={handleApplyFix}
            onDismissFlag={handleDismissFlag}
            onRerunQc={handleRerunQc}
            onRender={handleRender}
            onBack={reset}
            onToast={showToast}
          />
        )}

        {/* ── error ── */}
        {status === 'error' && (
          <div className="rounded-xl border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-600 dark:text-red-400 animate-slide-up">
            <div className="flex items-start gap-3">
              <svg className="mt-0.5 shrink-0" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
              <div className="space-y-2 flex-1">
                <span>{errorMsg}</span>
                <button onClick={reset} className="block text-xs underline opacity-70 hover:opacity-100">
                  Start over
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── done: images + caption ── */}
        {status === 'done' && (
          <>
            <Output
              status="done"
              data={{ images, slides, caption }}
              errorMsg=""
              stepMessage=""
              onToast={showToast}
            />
            <button
              onClick={reset}
              className="mx-auto block text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
            >
              Start over
            </button>
          </>
        )}
      </main>

      {/* ── Footer ─────────────────────────────────────────────── */}
      <footer className="text-center text-xs text-gray-400 dark:text-gray-600 py-6">
        Powered by Claude
      </footer>

      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  )
}

import React, { useState, useEffect, useCallback, useRef } from 'react'
import Form from './components/Form.jsx'
import HookPicker from './components/HookPicker.jsx'
import ImagePicker from './components/ImagePicker.jsx'
import SlideReview from './components/SlideReview.jsx'
import Output from './components/Output.jsx'
import Toast from './components/Toast.jsx'

// Stage flow:
// idle → template_selection → hooks_loading → hook_selection
//   Dark:  → image_selection → slides_loading → qc_loading → review → rendering → done
//   Light: → light_upload → light_generating → done
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

// ── Template selector ──────────────────────────────────────────────────────
function TemplateSelector({ onSelect, onBack }) {
  return (
    <div className="space-y-6 animate-slide-up">
      <div>
        <h2 className="text-xl font-semibold">Choose a template</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Select the visual style for your carousel.</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {/* Dark card */}
        <button
          onClick={() => onSelect('dark')}
          className="rounded-xl border border-gray-200 dark:border-gray-800 p-5 text-left hover:border-accent hover:bg-gray-50 dark:hover:bg-gray-900/60 transition-all group"
        >
          <div className="w-full h-28 rounded-lg bg-gradient-to-br from-gray-900 to-gray-950 mb-4 flex items-center justify-center border border-gray-700">
            <span className="text-white text-4xl font-black tracking-tight" style={{ fontFamily: 'serif' }}>Aa</span>
          </div>
          <div className="font-semibold text-sm group-hover:text-accent transition-colors">Dark Template</div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Bold headings on dark background with library image</div>
        </button>

        {/* Light card */}
        <button
          onClick={() => onSelect('light')}
          className="rounded-xl border border-gray-200 dark:border-gray-800 p-5 text-left hover:border-accent hover:bg-gray-50 dark:hover:bg-gray-900/60 transition-all group"
        >
          <div className="w-full h-28 rounded-lg bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-200 dark:to-gray-300 mb-4 flex items-center justify-center border border-gray-200">
            <span className="text-gray-900 text-4xl font-black tracking-tight" style={{ fontFamily: 'serif' }}>Aa</span>
          </div>
          <div className="font-semibold text-sm group-hover:text-accent transition-colors">Light Template</div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Image-driven slides — you upload one photo per slide</div>
        </button>
      </div>

      <button
        onClick={onBack}
        className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
      >
        ← Back
      </button>
    </div>
  )
}

// ── Light image upload ─────────────────────────────────────────────────────
function LightUpload({ onConfirm, onBack }) {
  const [files, setFiles] = useState([])
  const [previews, setPreviews] = useState([])

  const handleChange = (e) => {
    const selected = Array.from(e.target.files).slice(0, 8)
    setFiles(selected)
    // Revoke old previews
    previews.forEach(URL.revokeObjectURL)
    setPreviews(selected.map(f => URL.createObjectURL(f)))
  }

  useEffect(() => {
    return () => previews.forEach(URL.revokeObjectURL)
  }, [previews])

  const canGenerate = files.length >= 2

  return (
    <div className="space-y-6 animate-slide-up">
      <div>
        <h2 className="text-xl font-semibold">Upload your images</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Upload 2–8 images. Each image becomes one content slide. Claude will analyse each image and write the slide text.
        </p>
      </div>

      <label className="block cursor-pointer rounded-xl border-2 border-dashed border-gray-300 dark:border-gray-700 hover:border-accent transition-colors p-8 text-center">
        <input
          type="file"
          className="sr-only"
          accept="image/*"
          multiple
          onChange={handleChange}
        />
        <div className="flex flex-col items-center gap-2 text-gray-400 dark:text-gray-500">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/>
            <polyline points="21 15 16 10 5 21"/>
          </svg>
          <span className="text-sm">
            {files.length > 0
              ? `${files.length} image${files.length > 1 ? 's' : ''} selected — click to change`
              : 'Click to select images (2–8)'}
          </span>
        </div>
      </label>

      {previews.length > 0 && (
        <div className="grid grid-cols-4 gap-2">
          {previews.map((src, i) => (
            <div key={i} className="aspect-square rounded-lg overflow-hidden bg-gray-100 dark:bg-gray-800">
              <img src={src} alt={`Preview ${i + 1}`} className="w-full h-full object-cover" />
            </div>
          ))}
        </div>
      )}

      {files.length > 0 && files.length < 2 && (
        <p className="text-xs text-amber-600 dark:text-amber-400">Please select at least 2 images.</p>
      )}

      <div className="flex gap-3">
        <button
          onClick={onBack}
          className="flex-1 rounded-xl border border-gray-200 dark:border-gray-800 py-3 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
        >
          Back
        </button>
        <button
          onClick={() => onConfirm(files)}
          disabled={!canGenerate}
          className="flex-[2] rounded-xl bg-accent text-white py-3 text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed hover:bg-orange-600 transition-colors"
        >
          {canGenerate
            ? `Generate carousel (${files.length} slide${files.length > 1 ? 's' : ''})`
            : 'Select at least 2 images'}
        </button>
      </div>
    </div>
  )
}

// ── Main App ───────────────────────────────────────────────────────────────
export default function App() {
  const [dark, setDark]       = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  const [status, setStatus]   = useState('idle')
  const [stepMsg, setStepMsg] = useState('')
  const [errorMsg, setError]  = useState('')
  const [toast, setToast]     = useState(null)

  // Pipeline data
  const [topic,          setTopic]          = useState('')
  const [numSlides,      setNumSlides]      = useState(5)
  const [templateType,   setTemplateType]   = useState(null)   // 'dark' | 'light'
  const [hooks,          setHooks]          = useState([])
  const [selectedHook,   setSelectedHook]   = useState(null)
  const [selectedImage,  setSelectedImage]  = useState(null)
  const [slides,         setSlides]         = useState([])
  const [caption,        setCaption]        = useState('')
  const [carouselStyle,  setCarouselStyle]  = useState('dark_core')
  const [flags,          setFlags]          = useState([])
  const [images,         setImages]         = useState([])

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
    setTemplateType(null)
    setHooks([])
    setSelectedHook(null)
    setSelectedImage(null)
    setSlides([])
    setCaption('')
    setFlags([])
    setImages([])
  }, [])

  // ── Stage 1: form submit → template selection ──────────────────────────
  const handleFormSubmit = useCallback(({ topic: t, slides: n }) => {
    setTopic(t.trim())
    setNumSlides(n)
    setStatus('template_selection')
  }, [])

  // ── Stage 2: template selected → load hooks ────────────────────────────
  const handleTemplateSelect = useCallback(async (type) => {
    setTemplateType(type)
    setStatus('hooks_loading')
    setStepMsg('Generating hook options…')
    try {
      const res = await fetch('/hooks', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ topic, num_slides: numSlides }),
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
  }, [topic, numSlides, goError])

  // ── Stage 3a: hook selected — branch on template type ─────────────────
  const handleHookSelect = useCallback((hook) => {
    setSelectedHook(hook)
    if (templateType === 'light') {
      setStatus('light_upload')
    } else {
      setStatus('image_selection')
    }
  }, [templateType])

  // ── Dark pipeline: QC ─────────────────────────────────────────────────
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

  // ── Dark pipeline: image confirmed → generate slides (SSE) ────────────
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
          image_filename: image?.filename || null,
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
          let receivedSlides = event.slides || []
          if (receivedSlides.length > 0) {
            const last = receivedSlides[receivedSlides.length - 1]
            if (last && !last.heading?.trim().toLowerCase().startsWith('we show you')) {
              receivedSlides = [...receivedSlides.slice(0, -1), { ...last, heading: `We show you ${topic} every day.` }]
            }
          }
          setSlides(receivedSlides)
          setCaption(event.caption || '')
          setCarouselStyle(event.style || 'dark_core')
          runQc(receivedSlides)
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

  // ── Dark pipeline: per-slide regenerate ───────────────────────────────
  const handleRegenerate = useCallback(async (index, flag) => {
    const f = flag || {}
    try {
      const res = await fetch('/regenerate', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          topic,
          slide_index:    index,
          hook:           selectedHook?.hook || '',
          slides:         slidesRef.current,
          issue:          f.issue       || '',
          suggestion:     f.suggestion  || '',
          template_style: carouselStyle,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Regeneration failed (${res.status})`)
      }
      const data = await res.json()
      let regenerated = data.slide
      const isLast = index === slidesRef.current.length - 1
      if (isLast && regenerated) {
        const h = (regenerated.heading || '').trim().toLowerCase()
        if (!h.startsWith('we show you')) {
          regenerated = { ...regenerated, heading: `We show you ${topic} every day.` }
        } else if (!h.replace(/\.$/, '').endsWith('every day')) {
          regenerated = { ...regenerated, heading: regenerated.heading.replace(/\.?\s*$/, '') + ' every day.' }
        }
      }
      setSlides(prev => prev.map((s, i) => i === index ? regenerated : s))
      setFlags(prev => prev.filter(fl => (fl.slide_number - 1) !== index))
      showToast('Slide regenerated!')
    } catch (err) {
      showToast(err.message || 'Regeneration failed.', 'error')
    }
  }, [topic, selectedHook, carouselStyle, showToast])

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

  // ── Dark pipeline: render ──────────────────────────────────────────────
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
  }, [topic, carouselStyle, selectedImage, goError, showToast])

  // ── Light pipeline: images uploaded → generate-light (SSE) ───────────
  const handleLightGenerate = useCallback(async (imageFiles) => {
    setStatus('light_generating')
    setStepMsg('Analysing images…')

    const formData = new FormData()
    formData.append('topic', topic)
    formData.append('hook', selectedHook.hook)
    for (const file of imageFiles) {
      formData.append('images', file)
    }

    try {
      const res = await fetch('/generate-light', { method: 'POST', body: formData })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (${res.status})`)
      }
      let gotComplete = false
      let gotError = false
      await readSse(res, (event) => {
        if (event.step === 'complete') {
          gotComplete = true
          setImages(event.images || [])
          setCaption(event.caption || '')
          setStatus('done')
          showToast('Carousel generated!')
        } else if (event.step === 'error') {
          gotError = true
          goError(event.message || 'Generation failed.')
        } else {
          setStepMsg(event.message || '')
        }
      })
      if (!gotComplete && !gotError) goError('Generation did not complete.')
    } catch (err) {
      goError(err.message || 'Generation failed.')
    }
  }, [topic, selectedHook, goError, showToast])

  const LOADING_STAGES = new Set(['hooks_loading', 'slides_loading', 'qc_loading', 'rendering', 'light_generating'])

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
          <Form onGenerate={handleFormSubmit} loading={false} onReset={null} />
        )}

        {/* ── template selection ── */}
        {status === 'template_selection' && (
          <TemplateSelector
            onSelect={handleTemplateSelect}
            onBack={() => setStatus('idle')}
          />
        )}

        {/* ── loading spinner ── */}
        {LOADING_STAGES.has(status) && <LoadingPane message={stepMsg} />}

        {/* ── hook selection ── */}
        {status === 'hook_selection' && (
          <HookPicker
            hooks={hooks}
            topic={topic}
            onSelect={handleHookSelect}
            onBack={() => setStatus('template_selection')}
          />
        )}

        {/* ── dark: image selection ── */}
        {status === 'image_selection' && (
          <ImagePicker
            onSelect={handleImageConfirm}
            onBack={() => setStatus('hook_selection')}
          />
        )}

        {/* ── light: image upload ── */}
        {status === 'light_upload' && (
          <LightUpload
            onConfirm={handleLightGenerate}
            onBack={() => setStatus('hook_selection')}
          />
        )}

        {/* ── dark: slide review ── */}
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

import { useState, useRef, useEffect } from 'react'

const API_BASE = import.meta.env.VITE_API_URL || '';

function App() {
  const [file, setFile] = useState(null)
  const [initials, setInitials] = useState('JT')
  const [hourlyRate, setHourlyRate] = useState('516')
  const [headless, setHeadless] = useState(true)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(false)
  const [zipBlob, setZipBlob] = useState(null)

  // Progress state
  const [progressMessages, setProgressMessages] = useState([])
  const [currentStep, setCurrentStep] = useState(0)
  const [totalSteps, setTotalSteps] = useState(0)
  const progressEndRef = useRef(null)

  // win32print / CUPS state
  const [printCap, setPrintCap] = useState(null)   // null = unknown
  const [win32Installing, setWin32Installing] = useState(false)
  const [win32Message, setWin32Message] = useState(null)

  // Check print capability on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/win32print-status`)
      .then(r => r.json())
      .then(d => setPrintCap(d))
      .catch(() => setPrintCap({ available: false, platform: 'unknown', method: 'none', printer: null }))
  }, [])

  const scrollToBottom = () => {
    progressEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const handleInstallWin32 = async () => {
    setWin32Installing(true)
    setWin32Message(null)
    try {
      const res = await fetch(`${API_BASE}/api/install-win32print`, { method: 'POST' })

      const contentType = res.headers.get("content-type");
      if (contentType && contentType.includes("application/json")) {
        const data = await res.json()
        if (res.ok && data.success) {
          // Re-fetch full capability
          const cap = await fetch(`${API_BASE}/api/win32print-status`).then(r => r.json())
          setPrintCap(cap)
          setWin32Message({ type: 'success', text: data.message })
        } else {
          setWin32Message({ type: 'error', text: data.error || 'Installation failed.' })
        }
      } else {
        // This happens when Vercel proxies to a dead/unconfigured backend URL
        const text = await res.text();
        throw new Error(`The API proxy returned a non-JSON response (Status ${res.status}). Did you configure your backend URL in vercel.json?`);
      }
    } catch (err) {
      setWin32Message({ type: 'error', text: 'Could not reach server: ' + err.message })
    } finally {
      setWin32Installing(false)
    }
  }

  const [ignoreMismatch, setIgnoreMismatch] = useState(false)

  const handleSubmit = async (e, forceIgnore = false) => {
    if (e && e.preventDefault) e.preventDefault()
    if (!file) {
      setError('Please select an Excel file.')
      return
    }

    setError(null)
    setLoading(true)
    setSuccess(false)
    setProgressMessages([])
    setCurrentStep(0)
    setTotalSteps(0)

    const sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6)

    // Start SSE BEFORE the POST
    const evtSource = new EventSource(`${API_BASE}/api/progress/${sessionId}`)
    evtSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        setProgressMessages((prev) => [...prev, data.message])
        if (data.step !== undefined && data.step !== null) setCurrentStep(data.step)
        if (data.total !== undefined && data.total !== null) setTotalSteps(data.total)
        setTimeout(scrollToBottom, 50)
        if (data.status === 'done' || data.status === 'error') evtSource.close()
      } catch (_) { }
    }
    evtSource.onerror = () => evtSource.close()

    const formData = new FormData()
    formData.append('file', file)
    formData.append('initials', initials)
    formData.append('hourly_rate', hourlyRate)
    formData.append('headless', headless ? 'true' : 'false')
    formData.append('session_id', sessionId)
    formData.append('ignore_mismatch', forceIgnore ? 'true' : 'false')
    const isMobileDevice = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
    formData.append('is_mobile', isMobileDevice ? 'true' : 'false')

    try {
      const response = await fetch(`${API_BASE}/api/generate`, { method: 'POST', body: formData })

      if (!response.ok) {
        let errData
        const contentType = response.headers.get("content-type")
        if (contentType && contentType.includes("application/json")) {
          errData = await response.json()
        } else {
          throw new Error(`API returned Status ${response.status}. Proxy misconfigured?`)
        }

        if (response.status === 409 && errData.mismatch) {
          const proceed = window.confirm(
            "Mismatch Detected: The hourly rate you entered does not geometrically match the totals inside the spreadsheet.\n\n" +
            "Do you want to continue using your entered rate (" + hourlyRate + ") while prioritizing the exact hours extracted from the spreadsheet?"
          )
          if (proceed) {
            setIgnoreMismatch(true)
            evtSource.close()
            return await handleSubmit(null, true)
          } else {
            throw new Error("Action cancelled by user due to rate mismatch.")
          }
        }

        throw new Error(errData.error || 'Failed to generate timesheets')
      }

      const blob = await response.blob()
      if (blob.size < 100) throw new Error('ZIP appears empty. Check server logs.')
      setZipBlob(blob)
      setSuccess(true)
      setIgnoreMismatch(false)
    } catch (err) {
      setError(err.message)
    } finally {
      if (!forceIgnore) {
        setLoading(false)
        evtSource.close()
      }
    }
  }

  const handleDownload = () => {
    if (!zipBlob) return
    const url = window.URL.createObjectURL(zipBlob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'timesheets.zip'
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.URL.revokeObjectURL(url)
  }

  const handlePrint = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/print`, { method: 'POST' })
      const contentType = response.headers.get("content-type")
      if (contentType && contentType.includes("application/json")) {
        const data = await response.json()
        if (!response.ok) {
          alert(data.error + '\n\nPlease download the ZIP and print the PDFs manually.')
        } else {
          alert('✅ ' + data.message)
        }
      } else {
        throw new Error(`API proxy returned non-JSON response (Status ${response.status}). Check vercel.json backend configuration.`)
      }
    } catch (err) {
      alert('Error printing: ' + err.message)
    }
  }

  const win32Available = printCap?.available ?? null
  const progressPct = totalSteps > 0 ? Math.round((currentStep / totalSteps) * 100) : 0

  // Human-readable print method label
  const printMethodLabel = () => {
    if (!printCap) return 'Checking…'
    if (printCap.method === 'win32print') return `Windows Print (pywin32)${printCap.printer ? ' — ' + printCap.printer : ''}`
    if (printCap.method === 'cups') return `CUPS / lp${printCap.printer ? ' — ' + printCap.printer : ''}`
    return 'Not available'
  }

  const platformLabel = printCap?.platform
    ? printCap.platform.charAt(0).toUpperCase() + printCap.platform.slice(1)
    : 'Unknown'

  const installBtnLabel = () => {
    if (win32Installing) return 'Installing…'
    if (printCap?.platform === 'windows') return 'Install pywin32'
    return 'Check CUPS Status'
  }

  return (
    <div className="glass-panel">
      <h1>MSBM Punch Clock Printer</h1>
      <p className="subtitle">Automated bi-weekly punch clock calculator and PDF generator</p>

      {/* ── Print capability banner ──────────────────────────────────── */}
      <div className={`win32-banner ${win32Available === null ? 'win32-checking' : win32Available ? 'win32-ok' : 'win32-off'}`}>
        <div className="win32-badge">
          {win32Available === null ? '⏳' : win32Available ? '🖨️' : '⚠️'}
        </div>
        <div className="win32-body">
          <strong>
            {win32Available === null
              ? 'Checking print support…'
              : win32Available
                ? `Direct Printing Active — ${platformLabel}`
                : `Direct Printing Unavailable — ${platformLabel}`}
          </strong>
          <p>
            {win32Available === null
              ? 'Detecting available print method for this platform…'
              : win32Available
                ? <>Method: <strong>{printMethodLabel()}</strong>. The "Print All" button will send PDFs directly to your printer with no dialog.</>
                : 'CUPS (lp) is not found on this server. Your backend container requires the "cups-client" package built into the Dockerfile.'}
          </p>
          {win32Message && (
            <p className={`win32-msg ${win32Message.type === 'error' ? 'win32-msg-error' : 'win32-msg-success'}`}>
              {win32Message.text}
            </p>
          )}
        </div>
        {!win32Available && win32Available !== null && (
          <button
            id="btn-install-win32"
            className="win32-install-btn"
            onClick={handleInstallWin32}
            disabled={win32Installing}
          >
            {installBtnLabel()}
          </button>
        )}
      </div>
      {/* ────────────────────────────────────────────────────────────────── */}

      {error && <div className="error-banner">{error}</div>}

      {!loading && !success && (
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Excel Timesheet File</label>
            <input
              type="file"
              accept=".xlsx, .xls"
              onChange={(e) => setFile(e.target.files[0])}
              required
            />
          </div>

          <div className="form-group">
            <label>Employee Initials</label>
            <input
              type="text"
              placeholder="e.g. JT"
              value={initials}
              onChange={(e) => setInitials(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label>Hourly Rate</label>
            <input
              type="number"
              value={hourlyRate}
              onChange={(e) => setHourlyRate(e.target.value)}
              required
            />
          </div>

          <div className="checkbox-group" style={{ marginTop: '1rem' }}>
            <input
              type="checkbox"
              id="headless"
              checked={headless}
              onChange={(e) => setHeadless(e.target.checked)}
            />
            <label htmlFor="headless" style={{ marginBottom: 0 }}>
              Run Hidden (Headless Mode)
            </label>
          </div>

          <button type="submit" id="btn-generate">Generate Timesheets</button>
        </form>
      )}

      {loading && (
        <div className="loader-container">
          <div className="spinner"></div>
          <h3 className="loading-title">Processing Timesheets…</h3>

          {totalSteps > 0 && (
            <div className="progress-bar-container">
              <div className="progress-bar-track">
                <div className="progress-bar-fill" style={{ width: `${progressPct}%` }} />
              </div>
              <span className="progress-bar-label">{currentStep} / {totalSteps} PDFs ({progressPct}%)</span>
            </div>
          )}

          <div className="progress-log">
            {progressMessages.map((msg, idx) => (
              <div
                key={idx}
                className={`progress-line ${msg.includes('ERROR') || msg.includes('FAILED')
                  ? 'progress-error'
                  : msg.includes('OK') || msg.includes('saved')
                    ? 'progress-success'
                    : msg.includes('WARNING')
                      ? 'progress-warn'
                      : ''
                  }`}
              >
                <span className="progress-timestamp">{new Date().toLocaleTimeString()}</span>
                {msg}
              </div>
            ))}
            <div ref={progressEndRef} />
          </div>

          {!headless && (
            <p style={{ fontSize: '0.9em', color: 'var(--msbm-red)', marginTop: '1rem' }}>
              A browser window should appear showing the bot's progress.
            </p>
          )}
        </div>
      )}

      {success && (
        <div className="success-container glass-panel">
          <h2 className="success-title">🎉 Generation Complete!</h2>
          <p className="success-subtitle">Processed {progressMessages.length} steps successfully.</p>

          {progressMessages.length > 0 && (
            <details className="progress-details">
              <summary>View processing log ({progressMessages.length} events)</summary>
              <div className="progress-log" style={{ maxHeight: '200px' }}>
                {progressMessages.map((msg, idx) => (
                  <div
                    key={idx}
                    className={`progress-line ${msg.includes('ERROR') || msg.includes('FAILED') ? 'progress-error' : msg.includes('OK') || msg.includes('saved') ? 'progress-success' : ''
                      }`}
                  >
                    {msg}
                  </div>
                ))}
              </div>
            </details>
          )}

          <button id="btn-download" onClick={handleDownload}>⬇ Download ZIP Archive</button>
          <button id="btn-print" className="btn-secondary" onClick={handlePrint} disabled={!win32Available}>
            {win32Available
              ? `🖨️ Print All (${printCap?.method === 'cups' ? 'CUPS' : 'Windows Printer'})`
              : '🖨️ Print All (printing unavailable)'}
          </button>
          <button
            style={{ background: 'transparent', border: '1px solid #ccc', color: '#666', marginTop: '0.5rem' }}
            onClick={async () => {
              setSuccess(false);
              setProgressMessages([]);
              setFile(null);
              // Trigger manual server wipe via new endpoint
              try {
                await fetch(`${API_BASE}/api/cleanup`, { method: 'POST' });
              } catch (_) { }
            }}
          >
            ↩ Start Over
          </button>
        </div>
      )}
    </div>
  )
}

export default App

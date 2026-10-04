// Run with gstack: browse goto http://localhost:3000, then browse eval tests/browser/scan-controls.js.
// All /v1 requests are mocked in this page; no hardware or provider calls are made.
// Reload the page afterward to discard fixtures and restore the real API client.
return (async () => {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const waitFor = async (check, label, timeout = 9000) => {
    const until = Date.now() + timeout;
    while (!check()) {
      if (Date.now() > until) throw new Error(`Timed out: ${label}`);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  };
  const button = label => Array.from(document.querySelectorAll('button')).find(el => el.textContent.trim() === label);
  const idle = () => ({ job_id: null, trigger_id: null, status: 'idle', message: 'Ready to scan', events: [] });
  const result = text => ({accepted: true, text, reason: 'accepted', metrics: {}, capture_preview: null, openai_review_status: 'requested', openai_revision_status: 'skipped_high_confidence'});
  const fixture = {
    job: idle(), jobs: {}, calls: [], offline: false, sequence: 0,
    settings: {blink_only: false, revision: 'initial', applied_revision: 'initial', applied_blink_only: false, tracker_connected: true, eye_camera_index: 1},
    race: false, busyOnStart: false,
  };
  window.__loobQA = fixture;
  const originalFetch = window.fetch;
  window.__loobOriginalFetch = originalFetch;
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const path = url.pathname;
    if (!path.startsWith('/v1/')) return originalFetch(input, init);
    fixture.calls.push([init.method || 'GET', path]);
    if (fixture.offline) throw new TypeError('QA simulated disconnect');
    let body;
    if (path === '/v1/tracker-settings') {
      if (init.method === 'PATCH') {
        fixture.settings.blink_only = JSON.parse(init.body).blink_only;
        fixture.settings.revision = `setting-${++fixture.sequence}`;
      }
      body = fixture.settings;
    } else if (path === '/v1/diagnostics/eyes') {
      body = {connected:false, updated_at:null, events:[]};
    } else if (path === '/v1/scan-jobs/latest') {
      body = {...fixture.job}; delete body.result;
    } else if (path === '/v1/scan-jobs' && init.method === 'POST') {
      const request = JSON.parse(init.body);
      if (fixture.busyOnStart) {
        fixture.busyOnStart = false;
        fixture.job = {job_id:`other-${++fixture.sequence}`, status:'transcribing', message:'Reading text', events:[]};
        fixture.jobs[fixture.job.job_id] = fixture.job;
        return new Response(JSON.stringify({detail:'Scan skipped: another OCR scan is running.'}), {status:409, headers:{'Content-Type':'application/json'}});
      }
      fixture.job = {job_id:`scan-${++fixture.sequence}`, trigger_id:request.trigger_id, source:request.source, camera_index:request.camera_index, status:'framing', message:'Frame the page', events:[]};
      fixture.jobs[fixture.job.job_id] = fixture.job;
      body = fixture.job;
    } else if (path.endsWith('/preview')) {
      body = {...fixture.job, frame:null};
    } else if (path.endsWith('/capture')) {
      fixture.job.status = 'transcribing'; fixture.job.message = 'Reading text'; body = fixture.job;
    } else if (path.endsWith('/cancel')) {
      fixture.job.status = 'cancelling'; fixture.job.message = 'Releasing camera'; body = fixture.job;
    } else if (path.startsWith('/v1/scan-jobs/')) {
      const id = path.split('/').at(-1);
      body = fixture.jobs[id];
      if (fixture.race && id === 'race-a') { fixture.job = fixture.jobs['race-b']; fixture.race = false; }
    } else throw new Error(`Unexpected API call in QA: ${path}`);
    return new Response(JSON.stringify(body), {status:200, headers:{'Content-Type':'application/json'}});
  };
  await waitFor(() => !!document.querySelector('[aria-label="Open Developer view"]'), 'reader hydration');
  document.querySelector('[aria-label="Open Developer view"]').click();
  await waitFor(() => button('Scan OCR') && !button('Scan OCR').disabled, 'scan API reconnect');
  const toggle = document.querySelector('[role="switch"]');
  assert(toggle && !toggle.checked, 'blink-only should start off');
  await waitFor(() => !toggle.disabled, 'settings API reconnect');
  toggle.click();
  await waitFor(() => document.body.innerText.includes('Applying to eye tracker'), 'setting acknowledgement pending');
  assert(!document.body.innerText.includes('Blink-only active'), 'setting must not be active before acknowledgement');
  fixture.settings.applied_revision = fixture.settings.revision;
  fixture.settings.applied_blink_only = true;
  await waitFor(() => document.body.innerText.includes('Blink-only active'), 'setting acknowledged');
  button('Scan OCR').click();
  await waitFor(() => !!button('Capture now'), 'framing controls');
  assert(button('Scanning…').disabled && !button('Cancel scan').disabled, 'cancel must remain usable during scan');
  button('Capture now').click();
  await waitFor(() => document.body.innerText.includes('Reading text'), 'capture progresses');
  button('Cancel scan').click();
  await waitFor(() => !!button('Cancelling…'), 'cancellation acknowledged');
  assert(button('Scanning…').disabled, 'new scan must wait for worker exit');
  fixture.job.status = 'cancelled'; fixture.job.message = 'Scan cancelled; camera released.';
  await waitFor(() => button('Scan OCR') && !button('Scan OCR').disabled, 'cancelled scan rearms');
  button('Scan OCR').click();
  await waitFor(() => !!button('Capture now'), 'retry scan');
  fixture.offline = true;
  await waitFor(() => document.body.innerText.includes('Scan service offline'), 'disconnect visible');
  assert(button('Cancel scan').disabled, 'offline actions disabled');
  fixture.job = idle(); fixture.offline = false;
  await waitFor(() => button('Scan OCR') && !button('Scan OCR').disabled, 'API restart clears old job');
  const starts = () => fixture.calls.filter(([method, path]) => method === 'POST' && path === '/v1/scan-jobs').length;
  for (const label of ['Scan OCR', 'Test 3 blinks']) {
    const before = starts();
    fixture.busyOnStart = true;
    button(label).click();
    await waitFor(() => document.querySelector('.tool-status')?.textContent.includes('skipped'), `${label} busy request skipped`);
    assert(!document.querySelector('.tool-status').classList.contains('is-error'), 'busy skip should be a notice');
    assert(starts() === before + 1, 'busy start must only be submitted once');
    await waitFor(() => !!button('Scanning…'), 'existing scan progress recovered');
    assert(button('Scanning…').disabled && button('Test 3 blinks').disabled, 'both start controls disabled while busy');
    fixture.job.status = 'cancelled'; fixture.job.message = 'Camera released';
    await waitFor(() => button('Scan OCR') && !button('Scan OCR').disabled, 'finished scan releases controls');
    await new Promise(resolve => setTimeout(resolve, 2200));
    assert(starts() === before + 1, 'skipped request must not replay after completion');
  }
  button('Scan OCR').click();
  await waitFor(() => !!button('Capture now'), 'fresh explicit request starts after skipped requests');
  fixture.job.status = 'settling';
  fixture.job.settle_seconds = 8;
  fixture.job.scan_starts_at = Date.now() / 1000 + 8;
  fixture.job.message = 'Waiting 8.0s for page to settle.';
  await waitFor(() => document.querySelector('[aria-label="Page-turn wait"]')?.textContent.includes('OCR starts in 8s'), 'eight-second page-turn countdown');
  const startsBeforeWait = starts();
  await waitFor(() => document.querySelector('[aria-label="Page-turn wait"]')?.textContent.includes('OCR starts in 6s'), 'countdown advances');
  assert(button('Scanning…').disabled && button('Test 3 blinks').disabled, 'scan and blink controls blocked during page-turn wait');
  await waitFor(() => document.querySelector('[aria-label="Page-turn wait"]')?.textContent.includes('wait complete'), 'countdown completes');
  assert(starts() === startsBeforeWait, 'countdown must not submit an extra scan');
  fixture.job.status = 'framing';
  await waitFor(() => !!button('Capture now'), 'server begins capture after waiting');
  assert(!document.querySelector('[aria-label="Page-turn wait"]'), 'countdown clears when capture starts');
  fixture.jobs['race-a'] = {job_id:'race-a', status:'accepted', source:'manual', message:'First result', events:[], result:result('STALE_PAGE_QA')};
  fixture.jobs['race-b'] = {job_id:'race-b', status:'accepted', source:'manual', message:'New result', events:[], result:result('LATEST_PAGE_QA')};
  fixture.job = fixture.jobs['race-a']; fixture.race = true;
  await waitFor(() => fixture.calls.some(call => call[1] === '/v1/scan-jobs/race-b'), 'new terminal job result fetched');
  await waitFor(() => document.body.innerText.includes('Page scanned'), 'accepted result applied');
  document.querySelector('[aria-label="Return to Reader view"]').click();
  await waitFor(() => document.body.innerText.includes('LATEST_PAGE_QA'), 'latest OCR shown in reader');
  assert(!document.body.innerText.includes('STALE_PAGE_QA'), 'superseded OCR must not apply');
  return {passed:['setting pending and acknowledged','manual capture','cancel keeps gate until terminal','retry','disconnect and restart','manual and blink busy requests skipped without replay','fresh scan after completion','eight-second countdown blocks starts','new terminal result race'], calls:fixture.calls.length};
})()

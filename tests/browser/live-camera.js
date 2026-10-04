// Evaluate on a fresh local reader page. All /v1 requests are fixtures.
return (async () => {
  const assert = (value, label) => { if (!value) throw new Error(label); };
  const waitFor = async (check, label) => {
    const end = Date.now() + 9000;
    while (!check()) {
      if (Date.now() > end) throw new Error(`Timed out: ${label}`);
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  };
  const button = label => [...document.querySelectorAll('button')].find(el => el.textContent.trim() === label);
  const picture = () => document.querySelector('img[alt="Live OCR camera 2"]');
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 8;
  const jpeg = color => { canvas.getContext('2d').fillStyle=color; canvas.getContext('2d').fillRect(0,0,8,8); return canvas.toDataURL('image/jpeg'); };
  let image = jpeg('#aa2222');
  let starts = 0;
  let previews = 0;
  let job = {job_id:null, status:'idle', events:[]};
  const originalFetch = window.fetch;
  window.fetch = async (input, init = {}) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (!path.startsWith('/v1/')) return originalFetch(input,init);
    let body;
    if (path === '/v1/tracker-settings') body = {blink_only:false,revision:'fixture',applied_revision:'fixture',tracker_connected:false};
    else if (path === '/v1/diagnostics/eyes') body = {connected:false,updated_at:null,events:[]};
    else if (path === '/v1/scan-jobs/latest') { body = {...job}; delete body.result; }
    else if (path === '/v1/scan-jobs' && init.method === 'POST') {
      starts++;
      job = {job_id:'live-test',source:'manual',camera_index:2,status:'opening_camera',message:'Opening camera',events:[]}; body = job;
    } else if (path.endsWith('/preview')) {
      previews++;
      body = {...job,frame:job.status === 'framing' ? {data_url:image,width:8,height:8,captured_at:Date.now()/1000} : null};
    } else if (path.endsWith('/capture')) {
      job = {...job,status:'captured',message:'Page captured',result:{data_url:image,width:8,height:8,captured_at:Date.now()/1000,page_detected:true}}; body = job;
    } else if (path.endsWith('/cancel')) { job = {...job,status:'cancelled',message:'Camera released'}; body = job; }
    else if (path === '/v1/scan-jobs/live-test') body = job;
    else throw new Error(`Unexpected API call: ${path}`);
    return new Response(JSON.stringify(body), {status:200,headers:{'Content-Type':'application/json'}});
  };
  await waitFor(() => document.querySelector('[aria-label="Open Developer view"]'), 'reader');
  document.querySelector('[aria-label="Open Developer view"]').click();
  await waitFor(() => button('Live camera') && !button('Live camera').disabled, 'preview control');
  button('Live camera').click();
  await waitFor(() => document.querySelector('[aria-label="Live book camera"]'), 'opening preview');
  assert(!picture(), 'opening does not invent a frame');
  job = {...job,status:'framing',message:'Frame the page'};
  await waitFor(() => picture(), 'captured frame shown');
  const first = picture().src;
  image = jpeg('#2255aa');
  await waitFor(() => picture()?.src !== first, 'frames update');
  document.querySelector('[aria-label="Return to Reader view"]').click();
  await waitFor(() => !picture(), 'reader hides preview');
  const beforeHidden = previews;
  await new Promise(resolve => setTimeout(resolve,650));
  assert(previews === beforeHidden, 'hidden Developer panel stops preview requests');
  document.querySelector('[aria-label="Open Developer view"]').click();
  await waitFor(() => picture(), 'Developer panel resumes preview');
  button('Capture now').click();
  await waitFor(() => !document.querySelector('[aria-label="Live book camera"]'), 'capture closes live preview');
  await waitFor(() => document.querySelector('img[alt="Latest book camera capture"]'), 'captured image shown');
  assert(starts === 1, 'capture reuses the existing camera job');
  assert(!document.querySelector('.scan-word-overlay'), 'capture has no OCR word boxes');
  await waitFor(() => button('Live camera') && !button('Live camera').disabled, 'capture releases camera controls');
  button('Live camera').click();
  await waitFor(() => button('Cancel scan'), 'second live camera opens');
  button('Cancel scan').click();
  await waitFor(() => button('Live camera') && !button('Live camera').disabled, 'cancel releases camera');
  return {passed:['opening without fake frames','frames update','hidden preview stops polling','capture reuses camera job','captured JPEG without word boxes','camera release and cancellation'],starts,previews};
})()

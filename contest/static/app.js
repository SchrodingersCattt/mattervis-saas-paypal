const form = document.querySelector('#figure-form');
const fileInput = document.querySelector('#cif');
const promptInput = document.querySelector('#prompt');
const generate = document.querySelector('#generate');
const notice = document.querySelector('#notice');
let currentJob = null;

function flash(message, error = false) {
  notice.textContent = message;
  notice.classList.toggle('error', error);
  notice.hidden = false;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'The request failed.');
  return body;
}

fileInput.addEventListener('change', () => {
  document.querySelector('#file-name').textContent = fileInput.files[0]?.name || 'Choose a CIF file';
});

document.querySelector('#example').addEventListener('click', async () => {
  try {
    const response = await fetch('/static/example.cif');
    const file = new File([await response.blob()], 'example-urea.cif', {type: 'chemical/x-cif'});
    const transfer = new DataTransfer(); transfer.items.add(file); fileInput.files = transfer.files;
    fileInput.dispatchEvent(new Event('change'));
    promptInput.value = 'Create a clean ball-and-stick figure with hydrogens visible.';
    flash('Example structure loaded. You can change the direction before generating.');
  } catch { flash('Could not load the example structure.', true); }
});

function showJob(job) {
  currentJob = job;
  document.querySelector('#empty').hidden = true;
  const image = document.querySelector('#preview'); image.src = `/api/jobs/${job.id}/preview`; image.hidden = false;
  document.querySelector('#preview-tag').hidden = false;
  document.querySelector('#result').hidden = false;
  document.querySelector('#result-status').textContent = job.paid ? 'EXPORT UNLOCKED' : 'PREVIEW READY';
  document.querySelector('#result-title').textContent = job.filename;
  document.querySelector('#style-pill').textContent = job.plan.style.replaceAll('_', ' ');
  document.querySelector('#explanation').textContent = job.plan.explanation;
  document.querySelector('#checkout').hidden = job.paid;
  const download = document.querySelector('#download'); download.hidden = !job.paid; download.href = `/api/jobs/${job.id}/download`;
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  generate.disabled = true; generate.querySelector('span').textContent = 'Inspecting & rendering…';
  flash('Inspecting the structure, asking the model, and rendering a real preview.');
  try {
    const job = await requestJson('/api/jobs', {method: 'POST', body: new FormData(form)});
    showJob(job); flash('Preview ready. Review the plan before sandbox checkout.');
  } catch (error) { flash(error.message, true); }
  finally { generate.disabled = false; generate.querySelector('span').textContent = 'Generate preview'; }
});

document.querySelector('#checkout').addEventListener('click', async () => {
  if (!currentJob) return;
  const button = document.querySelector('#checkout'); button.disabled = true;
  try {
    const order = await requestJson(`/api/jobs/${currentJob.id}/checkout`, {method: 'POST'});
    window.location.assign(order.approval_url);
  } catch (error) { flash(error.message, true); button.disabled = false; }
});

const params = new URLSearchParams(window.location.search);
if (params.get('job')) {
  requestJson(`/api/jobs/${encodeURIComponent(params.get('job'))}`).then(job => {
    showJob(job);
    const state = params.get('payment');
    if (state === 'complete') flash('Sandbox payment verified. Your high-resolution export is unlocked.');
    if (state === 'cancelled') flash('Checkout was cancelled. Nothing was charged.');
    if (state === 'failed') flash('Payment could not be verified. The export remains locked.', true);
  }).catch(error => flash(error.message, true));
}

(function () {
  const slides = Array.from(document.querySelectorAll('.slide'));
  const counterEl = document.getElementById('slide-counter');
  const progressFill = document.getElementById('deck-progress-fill');
  const btnPrev = document.getElementById('btn-prev-slide');
  const btnNext = document.getElementById('btn-next-slide');
  const btnToggleMode = document.getElementById('btn-toggle-mode');
  const btnPrint = document.getElementById('btn-print-deck');

  let currentIdx = 0;
  let singleMode = true;

  function renderState() {
    const total = slides.length;
    if (singleMode) {
      document.body.classList.add('mode-single');
      slides.forEach((s, idx) => {
        if (idx === currentIdx) {
          s.classList.add('active');
        } else {
          s.classList.remove('active');
        }
      });
      if (btnToggleMode) btnToggleMode.textContent = '📜 Scroll All 10 Slides';
    } else {
      document.body.classList.remove('mode-single');
      slides.forEach((s) => s.classList.add('active'));
      if (btnToggleMode) btnToggleMode.textContent = '🎞️ Slide Mode';
    }

    if (counterEl) {
      counterEl.textContent = `${currentIdx + 1} / ${total}`;
    }
    if (progressFill) {
      const pct = Math.round(((currentIdx + 1) / total) * 100);
      progressFill.style.width = `${pct}%`;
    }
  }

  function goToSlide(idx) {
    if (!slides.length) return;
    currentIdx = Math.max(0, Math.min(slides.length - 1, idx));
    renderState();
    if (!singleMode && slides[currentIdx]) {
      slides[currentIdx].scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }

  if (btnPrev) {
    btnPrev.addEventListener('click', () => goToSlide(currentIdx - 1));
  }
  if (btnNext) {
    btnNext.addEventListener('click', () => goToSlide(currentIdx + 1));
  }
  if (btnToggleMode) {
    btnToggleMode.addEventListener('click', () => {
      singleMode = !singleMode;
      renderState();
    });
  }
  if (btnPrint) {
    btnPrint.addEventListener('click', () => {
      window.print();
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
    if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {
      if (singleMode) {
        e.preventDefault();
        goToSlide(currentIdx + 1);
      }
    } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
      if (singleMode) {
        e.preventDefault();
        goToSlide(currentIdx - 1);
      }
    }
  });

  renderState();
})();

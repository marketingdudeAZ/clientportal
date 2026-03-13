/**
 * Phase 4: Asset Library JavaScript
 * Tab filtering, lightbox, drag-and-drop upload, subcategory logic.
 */

(function () {
  'use strict';

  // ── Category Tab Filtering ──
  var tabs = document.querySelectorAll('.asset-tab');
  var grids = document.querySelectorAll('.asset-grid');

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var category = this.getAttribute('data-category');

      tabs.forEach(function (t) { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
      this.classList.add('active');
      this.setAttribute('aria-selected', 'true');

      grids.forEach(function (grid) {
        if (category === 'all') {
          grid.removeAttribute('hidden');
        } else {
          grid.hidden = grid.getAttribute('data-category') !== category;
        }
      });
    });
  });

  // ── Lightbox ──
  window.openLightbox = function (card) {
    var lightbox = document.getElementById('lightbox');
    var img = document.getElementById('lightbox-img');
    var name = document.getElementById('lightbox-name');
    var dl = document.getElementById('lightbox-download');

    var fileUrl = card.getAttribute('data-file-url');
    var assetName = card.getAttribute('data-asset-name');

    img.src = fileUrl;
    img.alt = assetName;
    name.textContent = assetName;
    dl.href = fileUrl;

    lightbox.classList.add('active');
    document.body.style.overflow = 'hidden';
  };

  window.closeLightbox = function () {
    var lightbox = document.getElementById('lightbox');
    lightbox.classList.remove('active');
    document.body.style.overflow = '';
    document.getElementById('lightbox-img').src = '';
  };

  // Close lightbox on overlay click
  var lightbox = document.getElementById('lightbox');
  if (lightbox) {
    lightbox.addEventListener('click', function (e) {
      if (e.target === lightbox) closeLightbox();
    });
  }

  // ── Archive Toggle ──
  window.toggleArchived = function (show) {
    // This would re-query HubDB with/without status filter
    // For now, just toggle UI state
    console.log('Show archived:', show);
  };

  // ── Upload: Drag & Drop ──
  var dropZone = document.getElementById('drop-zone');
  var fileInput = document.getElementById('file-input');
  var fileList = document.getElementById('file-list');
  var selectedFiles = [];

  if (dropZone) {
    dropZone.addEventListener('dragover', function (e) {
      e.preventDefault();
      dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragleave', function () {
      dropZone.classList.remove('drag-over');
    });

    dropZone.addEventListener('drop', function (e) {
      e.preventDefault();
      dropZone.classList.remove('drag-over');
      addFiles(e.dataTransfer.files);
    });
  }

  if (fileInput) {
    fileInput.addEventListener('change', function () {
      addFiles(this.files);
    });
  }

  var ALLOWED_TYPES = ['jpg', 'jpeg', 'png', 'webp', 'mp4', 'mov', 'pdf', 'ai', 'eps', 'psd', 'svg'];
  var MAX_SIZE = 100 * 1024 * 1024; // 100MB

  function addFiles(files) {
    var errEl = document.getElementById('upload-error');
    errEl.style.display = 'none';

    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      var ext = file.name.split('.').pop().toLowerCase();

      if (ALLOWED_TYPES.indexOf(ext) === -1) {
        errEl.textContent = 'File type not supported. Accepted: JPG, PNG, WEBP, MP4, MOV, PDF, AI, EPS, PSD, SVG';
        errEl.style.display = 'block';
        continue;
      }
      if (file.size > MAX_SIZE) {
        errEl.textContent = 'File exceeds 100MB limit. Please compress and try again.';
        errEl.style.display = 'block';
        continue;
      }
      selectedFiles.push(file);
    }
    renderFileList();
  }

  function renderFileList() {
    if (!fileList) return;
    fileList.innerHTML = '';
    selectedFiles.forEach(function (file, idx) {
      var item = document.createElement('div');
      item.className = 'file-list-item';
      item.innerHTML =
        '<span>' + escapeHtml(file.name) + ' <span class="file-size">(' + formatBytes(file.size) + ')</span></span>' +
        '<button class="file-remove" data-idx="' + idx + '">&times;</button>';
      fileList.appendChild(item);
    });

    fileList.querySelectorAll('.file-remove').forEach(function (btn) {
      btn.addEventListener('click', function () {
        selectedFiles.splice(parseInt(this.getAttribute('data-idx')), 1);
        renderFileList();
      });
    });
  }

  // ── Subcategory Logic ──
  var subcategories = {
    'Photography': ['Exterior', 'Interior', 'Amenity', 'Aerial', 'Neighborhood'],
    'Video': ['Ad Creative', 'Property Tour', 'Testimonial'],
  };

  window.updateSubcategories = function () {
    var catSelect = document.getElementById('upload-category');
    var subGroup = document.getElementById('subcategory-group');
    var subSelect = document.getElementById('upload-subcategory');

    var category = catSelect.value;
    var subs = subcategories[category];

    if (subs) {
      subSelect.innerHTML = '<option value="">Select subcategory\u2026</option>';
      subs.forEach(function (sub) {
        subSelect.innerHTML += '<option value="' + sub + '">' + sub + '</option>';
      });
      subGroup.style.display = '';
    } else {
      subGroup.style.display = 'none';
      subSelect.innerHTML = '';
    }
  };

  // ── Upload Form Submit ──
  var uploadForm = document.getElementById('upload-form');
  if (uploadForm) {
    uploadForm.addEventListener('submit', function (e) {
      e.preventDefault();

      if (selectedFiles.length === 0) {
        var errEl = document.getElementById('upload-error');
        errEl.textContent = 'Please select at least one file.';
        errEl.style.display = 'block';
        return;
      }

      var formData = new FormData();
      formData.append('property_uuid', uploadForm.querySelector('[name="property_uuid"]').value);
      formData.append('category', document.getElementById('upload-category').value);
      formData.append('subcategory', document.getElementById('upload-subcategory')?.value || '');
      formData.append('description', document.getElementById('upload-description').value);

      selectedFiles.forEach(function (file) {
        formData.append('files', file);
      });

      var progress = document.getElementById('upload-progress');
      var progressFill = document.getElementById('progress-fill');
      var progressText = document.getElementById('progress-text');
      var submitBtn = document.getElementById('upload-submit');

      progress.style.display = '';
      submitBtn.disabled = true;
      progressText.textContent = 'Uploading...';

      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/asset-upload');

      xhr.upload.addEventListener('progress', function (evt) {
        if (evt.lengthComputable) {
          var pct = Math.round((evt.loaded / evt.total) * 100);
          progressFill.style.width = pct + '%';
          progressText.textContent = 'Uploading... ' + pct + '%';
        }
      });

      xhr.addEventListener('load', function () {
        if (xhr.status >= 200 && xhr.status < 300) {
          progressText.textContent = 'Upload complete!';
          selectedFiles = [];
          renderFileList();
          // Refresh gallery after short delay
          setTimeout(function () {
            document.getElementById('upload-modal').classList.remove('active');
            progress.style.display = 'none';
            submitBtn.disabled = false;
            window.location.reload();
          }, 1500);
        } else {
          var errEl = document.getElementById('upload-error');
          errEl.textContent = 'Upload failed. Please try again.';
          errEl.style.display = 'block';
          progress.style.display = 'none';
          submitBtn.disabled = false;
        }
      });

      xhr.addEventListener('error', function () {
        var errEl = document.getElementById('upload-error');
        errEl.textContent = 'Upload failed. Please try again.';
        errEl.style.display = 'block';
        progress.style.display = 'none';
        submitBtn.disabled = false;
      });

      xhr.send(formData);
    });
  }

  // ── Helpers ──
  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
})();

document.addEventListener("DOMContentLoaded", () => {
    // Elements
    const tabUrlBtn = document.getElementById("tab-url-btn");
    const tabUploadBtn = document.getElementById("tab-upload-btn");
    const tabUrlPane = document.getElementById("tab-url-pane");
    const tabUploadPane = document.getElementById("tab-upload-pane");

    const youtubeUrlInput = document.getElementById("youtube-url-input");
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("file-input");
    const fileInfoChip = document.getElementById("file-info-chip");
    const fileNameSpan = document.getElementById("file-name-span");
    const fileRemoveBtn = document.getElementById("file-remove-btn");

    const clipCountSelect = document.getElementById("clip-count-select");
    const framingModeSelect = document.getElementById("framing-mode-select");
    const subtitleStyleSelect = document.getElementById("subtitle-style-select");
    const subtitlePill = document.getElementById("subtitle-pill");
    const captionOptions = document.getElementById("caption-options");
    const selectedCaptionMode = () => captionOptions.querySelector("input:checked").value;
    if (new URLSearchParams(location.search).get("subtitles") === "off") {
        captionOptions.querySelector('input[value="off"]').checked = true;
    }
    const cancelBtn = document.getElementById("cancel-job-btn");
    const whisperModelSelect = document.getElementById("whisper-model-select");

    const geminiKeyInput = document.getElementById("gemini-key-input");
    const groqKeyInput = document.getElementById("groq-key-input");
    const openaiKeyInput = document.getElementById("openai-key-input");

    const generateBtn = document.getElementById("generate-btn");
    const progressCard = document.getElementById("progress-card");
    const progressBarFill = document.getElementById("progress-bar-fill");
    const progressStatusText = document.getElementById("progress-status-text");
    const progressPercentText = document.getElementById("progress-percent-text");

    const resultsSection = document.getElementById("results-section");
    const clipsGrid = document.getElementById("clips-grid");
    const clipsCountTitle = document.getElementById("clips-count-title");
    const downloadZipBtn = document.getElementById("download-zip-btn");

    let currentSourceType = "url"; // 'url' or 'upload'
    let uploadedFilename = null;
    let pollInterval = null;
    let activeJob = null;
    let requestingCancel = false;
    function updateCaptionControls() {
        const off = selectedCaptionMode() === "off";
        subtitleStyleSelect.disabled = off;
        subtitlePill.hidden = off;
        whisperModelSelect.disabled = off && document.getElementById("scan-mode").value !== "full";
    }
    const selection = document.getElementById("clip-selection");
    const scan = document.getElementById("scan-mode");
    function updateSelection() {
        const manual = selection.value === "range";
        for (const id of ["start-group", "end-group"]) document.getElementById(id).hidden = !manual;
        for (const control of [clipCountSelect, document.getElementById("clip-seconds")]) {
            control.closest(".setting-group").hidden = manual;
        }
        scan.querySelector('[value="full"]').disabled = manual;
        if (manual && scan.value === "full") scan.value = "fast-hd";
        scan.parentElement.querySelector("small").hidden = manual;
        updateCaptionControls();
    }
    function readTime(id) {
        const field = document.getElementById(id);
        const parts = field.value.trim().split(":");
        if (parts.length > 3 || parts.some((n, i) =>
            !/^\d+$/.test(n) || (i > 0 && (n.length > 2 || Number(n) > 59)))) {
            field.focus();
            throw new Error("Enter a time in HH:MM:SS, MM:SS, or whole seconds.");
        }
        const seconds = parts.reduce((s, n) => s * 60 + Number(n), 0);
        if (!Number.isSafeInteger(seconds)) throw new Error("Timestamp is too large.");
        return seconds;
    }
    if (new URLSearchParams(location.search).get("selection") === "range") selection.value = "range";
    selection.addEventListener("change", updateSelection);
    updateSelection();
    captionOptions.addEventListener("change", updateCaptionControls);
    document.getElementById("scan-mode").addEventListener("change", updateCaptionControls);
    updateCaptionControls();

    cancelBtn.addEventListener("click", async () => {
        const jobId = activeJob;
        if (!jobId || requestingCancel) return;
        requestingCancel = true;
        cancelBtn.disabled = true;
        cancelBtn.textContent = "Stopping…";
        try {
            const response = await fetch(`/api/cancel/${jobId}`, { method: "POST" });
            if (!response.ok) throw new Error("Could not stop this job. Try again.");
            if (activeJob === jobId) showStatus(jobId, await response.json());
        } catch (error) {
            if (activeJob === jobId) {
                requestingCancel = false;
                cancelBtn.disabled = false;
                cancelBtn.textContent = "Cancel generation";
                progressStatusText.textContent = error.message;
            }
        }
    });

    // Subtitle style colors mapping for pill preview
    const subtitleColors = {
        "neon_yellow": { text: "HIGHLIGHTED WORD", color: "#ffe600" },
        "electric_green": { text: "HIGHLIGHTED WORD", color: "#00ff66" },
        "vibrant_cyan": { text: "HIGHLIGHTED WORD", color: "#00f0ff" },
        "sunset_orange": { text: "HIGHLIGHTED WORD", color: "#ff8000" }
    };

    function updateSubtitlePill() {
        const style = subtitleStyleSelect.value;
        const config = subtitleColors[style] || subtitleColors["neon_yellow"];
        subtitlePill.innerHTML = `THE <span style="color: ${config.color}">${config.text}</span> POPS`;
    }

    subtitleStyleSelect.addEventListener("change", updateSubtitlePill);
    updateSubtitlePill();

    // Tab switching
    tabUrlBtn.addEventListener("click", () => {
        currentSourceType = "url";
        tabUrlBtn.classList.add("active");
        tabUploadBtn.classList.remove("active");
        tabUrlPane.classList.add("active");
        tabUploadPane.classList.remove("active");
    });

    tabUploadBtn.addEventListener("click", () => {
        currentSourceType = "upload";
        tabUploadBtn.classList.add("active");
        tabUrlBtn.classList.remove("active");
        tabUploadPane.classList.add("active");
        tabUrlPane.classList.remove("active");
    });

    // File Dropzone Handling
    dropzone.addEventListener("click", () => fileInput.click());

    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
    });

    dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
    });

    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files.length > 0) {
            handleFileUpload(fileInput.files[0]);
        }
    });

    fileRemoveBtn.addEventListener("click", () => {
        uploadedFilename = null;
        fileInput.value = "";
        fileInfoChip.classList.remove("active");
        dropzone.style.display = "block";
    });

    async function handleFileUpload(file) {
        const formData = new FormData();
        formData.append("file", file);

        dropzone.innerHTML = `<div class="spinner"></div><p style="margin-top:0.75rem;">Uploading <strong>${file.name}</strong>...</p>`;

        try {
            const resp = await fetch("/api/upload", {
                method: "POST",
                body: formData
            });
            const data = await resp.json();
            if (data.status === "success") {
                uploadedFilename = data.filename;
                fileNameSpan.textContent = `${data.filename} (${Math.round(data.duration)}s)`;
                dropzone.style.display = "none";
                fileInfoChip.classList.add("active");
            } else {
                alert("Upload failed: " + (data.detail || "Unknown error"));
                resetDropzone();
            }
        } catch (err) {
            alert("Upload failed: " + err.message);
            resetDropzone();
        }
    }

    function resetDropzone() {
        dropzone.style.display = "block";
        dropzone.innerHTML = `
            <div class="dropzone-icon">📁</div>
            <p><strong>Click to browse</strong> or drag & drop video file</p>
            <p style="font-size: 0.8rem; margin-top: 0.35rem;">MP4, MKV, MOV, WebM, AVI</p>
        `;
    }

    // Process Button Click
    generateBtn.addEventListener("click", async () => {
        let sourceValue = "";
        if (currentSourceType === "url") {
            sourceValue = youtubeUrlInput.value.trim();
            if (!sourceValue) {
                alert("Please paste a valid YouTube or Live Stream URL.");
                youtubeUrlInput.focus();
                return;
            }
        } else {
            if (!uploadedFilename) {
                alert("Please select and upload a video file first.");
                return;
            }
            sourceValue = uploadedFilename;
        }

        // Prepare payload
        const payload = {
            fast_mode: document.getElementById("scan-mode").value !== "full",
            clip_seconds: Number(document.getElementById("clip-seconds").value),
            fast_resolution: document.getElementById("scan-mode").value === "fast-hd" ? 1080 : 720,
            source_type: currentSourceType,
            source_value: sourceValue,
            clip_count: parseInt(clipCountSelect.value, 10),
            framing_mode: framingModeSelect.value,
            subtitle_style: subtitleStyleSelect.value,
            subtitle_mode: selectedCaptionMode(),
            burn_subtitles: selectedCaptionMode() === "burn",
            whisper_model: whisperModelSelect ? whisperModelSelect.value : "tiny",
            gemini_key: geminiKeyInput.value.trim() || null,
            groq_key: groqKeyInput.value.trim() || null,
            openai_key: openaiKeyInput.value.trim() || null
        };

        if (selection.value === "range") {
            try {
                payload.start_seconds = readTime("range-start");
                payload.end_seconds = readTime("range-end");
                if (payload.end_seconds <= payload.start_seconds) throw new Error("End time must be after start time.");
                payload.clip_count = 1;
                delete payload.clip_seconds;
            } catch (error) {
                alert(error.message);
                return;
            }
        }

        // UI transitions
        generateBtn.disabled = true;
        generateBtn.innerHTML = `<div class="spinner"></div> Processing Video...`;
        progressCard.classList.add("active");
        resultsSection.classList.remove("active");
        clipsGrid.innerHTML = "";

        updateProgressUI(5.0, "Starting auto-clipping job...");

        try {
            const resp = await fetch("/api/process", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || "Failed to start processing job");
            }

            const data = await resp.json();
            const jobId = data.job_id;

            // Start polling status
            startStatusPolling(jobId);

        } catch (err) {
            alert("Error: " + err.message);
            generateBtn.disabled = false;
            generateBtn.innerHTML = `✨ Generate Viral Shorts`;
            progressCard.classList.remove("active");
        }
    });

    function updateProgressUI(percent, message) {
        progressBarFill.style.width = `${percent}%`;
        progressPercentText.textContent = `${Math.round(percent)}%`;
        progressStatusText.textContent = message;
    }

    function showStatus(jobId, status) {
        if (activeJob !== jobId) return;
        updateProgressUI(status.percent ?? 0, status.message || "Working…");
        if (status.clips?.length) renderResults(status);
        if (["completed", "cancelled", "error"].includes(status.stage)) {
            clearInterval(pollInterval);
            activeJob = null;
            sessionStorage.removeItem("autoclip-active-job");
            cancelBtn.hidden = true;
            generateBtn.disabled = false;
            generateBtn.textContent = "Generate shorts";
        } else if (status.stage === "cancelling") {
            cancelBtn.disabled = true;
            cancelBtn.textContent = "Stopping…";
        }
    }

    function startStatusPolling(jobId) {
        clearInterval(pollInterval);
        activeJob = jobId;
        sessionStorage.setItem("autoclip-active-job", jobId);
        requestingCancel = false;
        cancelBtn.hidden = false;
        cancelBtn.disabled = false;
        cancelBtn.textContent = "Cancel generation";
        generateBtn.disabled = true;
        progressCard.classList.add("active");
        let fetching = false;
        async function poll() {
            if (fetching || activeJob !== jobId) return;
            fetching = true;
            try {
                const response = await fetch(`/api/status/${jobId}`, { cache: "no-store" });
                if (response.status === 404) {
                    showStatus(jobId, {stage: "error", message: "This job is no longer available."});
                } else if (response.ok) {
                    showStatus(jobId, await response.json());
                }
            } catch (error) {
                if (activeJob === jobId) progressStatusText.textContent = "Reconnecting to the studio…";
            } finally {
                fetching = false;
            }
        }
        pollInterval = setInterval(poll, 1000);
        poll();
    }
    const resumedJob = sessionStorage.getItem("autoclip-active-job");
    if (resumedJob) startStatusPolling(resumedJob);

    function renderResults(jobStatus) {
        const clips = jobStatus.clips || [];
        clipsCountTitle.textContent = `Generated ${clips.length} Viral 9:16 Shorts`;
        downloadZipBtn.href = jobStatus.zip_url || `/api/download-all/${jobStatus.job_id}`;
        downloadZipBtn.hidden = !jobStatus.zip_url;
        downloadZipBtn.style.display = jobStatus.zip_url ? "" : "none";

        clips.slice(clipsGrid.children.length).forEach((clip) => {
            const card = document.createElement("div");
            card.className = "clip-card";

            card.innerHTML = `
                <div class="video-container">
                    <video src="${clip.mp4_url}" poster="${clip.thumb_url}" controls playsinline preload="metadata"></video>
                    <div class="virality-badge">${clip.virality_score ? "🔥 " + clip.virality_score + "% VIRAL" : "FAST CLIP"}</div>
                    <div class="duration-badge">${clip.duration}s</div>
                </div>
                <div class="clip-details">
                    <div>
                        <h4 class="clip-title"></h4>
                        <p class="clip-reason"></p>
                    </div>
                    <div class="clip-actions">
                        <a href="${clip.mp4_url}" download="${clip.filename}" class="btn-download-mp4">
                            ⬇️ Download MP4
                        </a>
                    </div>
                </div>
            `;
            card.querySelector(".clip-title").textContent = clip.title;
            card.querySelector(".clip-reason").textContent = clip.reason || clip.hook || "";
            for (const [label, url] of [["SRT", clip.srt_url], ["ASS", clip.ass_url]]) {
                if (!url) continue;
                const link = document.createElement("a");
                link.className = "btn-sub";
                link.textContent = label;
                link.href = url;
                link.download = clip.filename.replace(/\.mp4$/i, "." + label.toLowerCase());
                card.querySelector(".clip-actions").appendChild(link);
            }
            clipsGrid.appendChild(card);
        });

        resultsSection.classList.add("active");

    }
});

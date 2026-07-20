class VADProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    const framesPerSecond = sampleRate / 128;

    // ── Configuration ────────────────────────────────────────────────────────
    this.useSpeechThreshold = true;
    this.useSilenceThreshold = true;

    // ── Noise Floor Estimation (only updates in IDLE state) ────────────────
    this._noiseFloorAlpha = 0.05; // Slow adaptation for noise floor
    this._noiseFloor = 0.005; // Initial noise floor estimate
    this._noiseFloorSamples = 0;
    this._noiseFloorMinSamples = 50;

    // ── Speech Thresholds with Hysteresis ───────────────────────────────────
    this._speechMargin = 0.015; // Margin above noise floor for speech start
    this._speechEndMargin = 0.008; // Margin above noise floor for speech end

    // ── Timing Parameters ───────────────────────────────────────────────────
    this._speechConfirmFrames = Math.round(framesPerSecond * 0.1); // 100ms to confirm speech
    this._silenceConfirmFrames = Math.round(framesPerSecond * 0.15); // 150ms to confirm silence
    this._minSpeechFrames = Math.round(framesPerSecond * 0.3); // 300ms minimum speech duration

    // ── Dynamic Endpointing ───────────────────────────────────────────────────
    this._baseSilenceSeconds = 1.5; // Base silence timeout
    this._maxSilenceSeconds = 4.0; // Maximum silence timeout
    this._silenceGrowthFactor = 0.1; // How much silence timeout grows per second of speech

    // ── State Machine ───────────────────────────────────────────────────────
    this._state = 'IDLE'; // IDLE, POSSIBLE_SPEECH, SPEAKING, POSSIBLE_END, ENDED
    this._stateFrameCount = 0;
    this._speechStartFrame = 0;
    this._speechDurationFrames = 0;
    this._peakRms = 0;

    this.port.onmessage = (event) => {
      if (event.data.type === 'set_threshold' && event.data.peakRms) {
        // Adapt threshold to ~30% of their peak volume, bounded to prevent getting stuck
        const newMargin = Math.max(0.015, Math.min(0.15, event.data.peakRms * 0.3));
        this._speechMargin = newMargin;
        this._speechEndMargin = newMargin * 0.5;
        this.port.postMessage({ type: 'debug', message: 'Adapted speech margin to ' + newMargin.toFixed(4) });
      }
    };

    // ── Volume Reporting ────────────────────────────────────────────────────
    this._volumeFrameCount = 0;
    this._volumeEmitEvery = Math.round(framesPerSecond / 20);
  }

  _getSpeechThreshold() {
    return this._noiseFloor + this._speechMargin;
  }

  _getSpeechEndThreshold() {
    return this._noiseFloor + this._speechEndMargin;
  }

  _getSilenceTimeoutFrames() {
    // Dynamic silence timeout based on speech duration
    const speechSeconds = this._speechDurationFrames / (sampleRate / 128);
    const additionalSilence = Math.min(
      speechSeconds * this._silenceGrowthFactor,
      this._maxSilenceSeconds - this._baseSilenceSeconds
    );
    const totalSilenceSeconds = this._baseSilenceSeconds + additionalSilence;
    return Math.round((sampleRate / 128) * totalSilenceSeconds);
  }

  _updateNoiseFloor(rms) {
    // Only update noise floor when we're confident user is NOT speaking
    if (this._state === 'IDLE') {
      this._noiseFloorSamples++;
      this._noiseFloor = this._noiseFloorAlpha * rms + (1 - this._noiseFloorAlpha) * this._noiseFloor;
    }
  }

  _transitionToState(newState) {
    this._state = newState;
    this._stateFrameCount = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    // Compute RMS
    let sumSquares = 0;
    for (let i = 0; i < channel.length; i++) {
      sumSquares += channel[i] * channel[i];
    }
    const rms = Math.sqrt(sumSquares / channel.length);

    // Update noise floor (only in IDLE state)
    this._updateNoiseFloor(rms);

    const speechThreshold = this._getSpeechThreshold();
    const speechEndThreshold = this._getSpeechEndThreshold();
    const aboveSpeechThreshold = !this.useSpeechThreshold || rms >= speechThreshold;

    // State machine
    switch (this._state) {
      case 'IDLE':
        if (aboveSpeechThreshold) {
          this._transitionToState('POSSIBLE_SPEECH');
        }
        break;

      case 'POSSIBLE_SPEECH':
        this._stateFrameCount++;
        if (aboveSpeechThreshold) {
          if (this._stateFrameCount >= this._speechConfirmFrames) {
            this._transitionToState('SPEAKING');
            this._speechStartFrame = this._stateFrameCount;
            this._peakRms = rms;
            this.port.postMessage({ type: 'speech_start' });
          }
        } else {
          // Noise burst, return to IDLE
          this._transitionToState('IDLE');
        }
        break;

      case 'SPEAKING':
        this._speechDurationFrames++;
        if (rms > this._peakRms) this._peakRms = rms;
        if (!aboveSpeechThreshold) {
          this._transitionToState('POSSIBLE_END');
        }
        break;

      case 'POSSIBLE_END':
        this._stateFrameCount++;
        if (aboveSpeechThreshold) {
          // Speech resumed, return to SPEAKING
          this._transitionToState('SPEAKING');
        } else {
          const silenceTimeoutFrames = this._getSilenceTimeoutFrames();
          if (this._stateFrameCount >= this._silenceConfirmFrames) {
            // Confirmed silence, start counting toward end
            if (this._stateFrameCount >= silenceTimeoutFrames) {
              // Check minimum speech duration
              if (this._speechDurationFrames >= this._minSpeechFrames) {
                this._transitionToState('ENDED');
                this.port.postMessage({ type: 'speech_end', peakRms: this._peakRms });
              } else {
                // Too short, treat as noise
                this.port.postMessage({ type: 'debug', message: 'Speech too short, treating as noise', duration: this._speechDurationFrames });
                this._transitionToState('IDLE');
              }
            }
          }
        }
        break;

      case 'ENDED':
        // Reset after sending speech_end
        this._speechDurationFrames = 0;
        this._transitionToState('IDLE');
        break;
    }

    // Volume reporting
    this._volumeFrameCount++;
    if (this._volumeFrameCount >= this._volumeEmitEvery) {
      this._volumeFrameCount = 0;
      this.port.postMessage({
        type: 'volume',
        rms,
        threshold: speechThreshold,
        state: this._state,
        noiseFloor: this._noiseFloor
      });
    }

    return true;
  }
}

registerProcessor('vad-processor', VADProcessor);
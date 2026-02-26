/**
 * AudioWorklet processor for capturing 16kHz 16-bit PCM audio.
 *
 * Runs in a separate audio thread for low-latency capture.
 * Buffers samples and sends chunks to the main thread via port.postMessage.
 */

class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = [];
    this.bufferSize = 4096; // Send every 4096 samples (~256ms at 16kHz)
  }

  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (input.length === 0) return true;

    const channelData = input[0]; // Mono
    if (!channelData) return true;

    // Accumulate samples
    for (let i = 0; i < channelData.length; i++) {
      this.buffer.push(channelData[i]);
    }

    // Send when buffer is full
    if (this.buffer.length >= this.bufferSize) {
      this.port.postMessage(new Float32Array(this.buffer));
      this.buffer = [];
    }

    return true;
  }
}

registerProcessor('pcm-capture', PCMCaptureProcessor);

import type { NarrationMood, StoryEffect } from "@/lib/api";

type Layer = {
  gain: GainNode;
  sources: Array<AudioBufferSourceNode | OscillatorNode>;
};

const MAX_AMBIENCE_GAIN = 0.32;
const MAX_EFFECT_GAIN = 0.55;

/** Quiet, generated soundscapes that do not require another audio service. */
export class AmbientSound {
  private context: AudioContext | null = null;
  private active: Layer | null = null;
  private activeEffect: Layer | null = null;
  private mood: NarrationMood = "neutral";
  private volume = 0.6;

  /** Start the audio context directly from a reader gesture. */
  async unlock() {
    if (!this.context) this.context = new AudioContext();
    await this.context.resume();
  }

  setVolume(volume: number) {
    this.volume = Math.max(0, Math.min(1, volume));
    if (!this.context) return;
    const now = this.context.currentTime;
    this.active?.gain.gain.setTargetAtTime(this.volume * MAX_AMBIENCE_GAIN, now, 0.12);
    this.activeEffect?.gain.gain.setTargetAtTime(this.volume * MAX_EFFECT_GAIN, now, 0.12);
  }

  /** Play one short event sound over the current ambience. */
  playEffect(effect: StoryEffect) {
    const context = this.context;
    if (!context || context.state !== "running" || this.volume === 0) return;
    const now = context.currentTime;
    if (this.activeEffect) {
      this.activeEffect.gain.gain.setTargetAtTime(0, now, 0.03);
    }
    const layer = this.createEffect(context, effect);
    this.activeEffect = layer;
    let remaining = layer.sources.length;
    for (const source of layer.sources) {
      source.onended = () => {
        source.disconnect();
        if (--remaining === 0) {
          layer.gain.disconnect();
          if (this.activeEffect === layer) this.activeEffect = null;
        }
      };
    }
  }

  async setMood(mood: NarrationMood) {
    if (mood === "neutral" && !this.context) return;
    if (mood === this.mood && this.context) return;
    if (!this.context) this.context = new AudioContext();
    const context = this.context;
    await context.resume();
    if (this.context !== context) return;

    const now = context.currentTime;
    if (this.active) {
      const old = this.active;
      old.gain.gain.cancelScheduledValues(now);
      old.gain.gain.setTargetAtTime(0, now, 0.18);
      let remaining = old.sources.length;
      for (const source of old.sources) {
        source.onended = () => {
          source.disconnect();
          if (--remaining === 0) old.gain.disconnect();
        };
        source.stop(now + 0.9);
      }
    }

    this.mood = mood;
    this.active = mood === "neutral" ? null : this.createLayer(context, mood);
    if (this.active) {
      this.active.gain.gain.setTargetAtTime(this.volume * MAX_AMBIENCE_GAIN, now, 0.2);
    }
  }

  stop() {
    const context = this.context;
    this.context = null;
    this.active = null;
    this.activeEffect = null;
    this.mood = "neutral";
    if (context) void context.close().catch(() => undefined);
  }

  private createLayer(context: AudioContext, mood: Exclude<NarrationMood, "neutral">): Layer {
    const gain = context.createGain();
    gain.gain.value = 0;
    gain.connect(context.destination);
    const sources: Layer["sources"] = [];

    const noise = context.createBuffer(1, context.sampleRate * 3, context.sampleRate);
    const samples = noise.getChannelData(0);
    for (let index = 0; index < samples.length; index++) {
      samples[index] = Math.random() * 2 - 1;
    }
    const wind = context.createBufferSource();
    wind.buffer = noise;
    wind.loop = true;
    const filter = context.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = mood === "suspense" ? 360 : 900;
    const windGain = context.createGain();
    windGain.gain.value = mood === "suspense" ? 0.34 : 0.12;
    wind.connect(filter).connect(windGain).connect(gain);
    wind.start();
    sources.push(wind);

    const frequencies = mood === "suspense" ? [55, 82.4] : [196, 293.7];
    frequencies.forEach((frequency, index) => {
      const tone = context.createOscillator();
      tone.type = mood === "suspense" ? "sine" : "triangle";
      tone.frequency.value = frequency;
      const toneGain = context.createGain();
      toneGain.gain.value = mood === "suspense" ? (index ? 0.1 : 0.22) : 0.055;
      tone.connect(toneGain).connect(gain);
      tone.start();
      sources.push(tone);
    });

    return { gain, sources };
  }

  private createEffect(context: AudioContext, effect: StoryEffect): Layer {
    const now = context.currentTime;
    const gain = context.createGain();
    gain.gain.value = this.volume * MAX_EFFECT_GAIN;
    gain.connect(context.destination);
    const sources: Layer["sources"] = [];

    const tone = (delay: number, duration: number, from: number, to: number, peak: number, shape: OscillatorType = "sine") => {
      const source = context.createOscillator();
      source.type = shape;
      source.frequency.setValueAtTime(from, now + delay);
      source.frequency.exponentialRampToValueAtTime(to, now + delay + duration);
      const envelope = context.createGain();
      envelope.gain.setValueAtTime(0.001, now + delay);
      envelope.gain.linearRampToValueAtTime(peak, now + delay + Math.min(0.08, duration / 4));
      envelope.gain.exponentialRampToValueAtTime(0.001, now + delay + duration);
      source.connect(envelope).connect(gain);
      source.start(now + delay);
      source.stop(now + delay + duration + 0.02);
      sources.push(source);
    };
    const noise = (delay: number, duration: number, frequency: number, peak: number) => {
      const buffer = context.createBuffer(1, Math.ceil(context.sampleRate * duration), context.sampleRate);
      const samples = buffer.getChannelData(0);
      for (let index = 0; index < samples.length; index++) samples[index] = Math.random() * 2 - 1;
      const source = context.createBufferSource();
      source.buffer = buffer;
      const filter = context.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = frequency;
      const envelope = context.createGain();
      envelope.gain.setValueAtTime(0.001, now + delay);
      envelope.gain.linearRampToValueAtTime(peak, now + delay + Math.min(0.08, duration / 4));
      envelope.gain.exponentialRampToValueAtTime(0.001, now + delay + duration);
      source.connect(filter).connect(envelope).connect(gain);
      source.start(now + delay);
      source.stop(now + delay + duration);
      sources.push(source);
    };

    if (effect === "door_creak") {
      tone(0, 1.05, 280, 85, 0.32, "sawtooth");
      noise(0.05, 0.85, 900, 0.16);
    } else if (effect === "footsteps") {
      tone(0.04, 0.22, 110, 45, 0.42);
      tone(0.42, 0.22, 105, 45, 0.38);
      noise(0.04, 0.17, 450, 0.18);
      noise(0.42, 0.17, 450, 0.16);
    } else if (effect === "thunder") {
      noise(0, 1.9, 300, 0.5);
      tone(0.04, 1.6, 65, 35, 0.25);
    } else {
      for (const delay of [0.02, 0.25, 0.49]) {
        tone(delay, 0.14, 190, 80, 0.42);
        noise(delay, 0.1, 800, 0.2);
      }
    }

    return { gain, sources };
  }
}

// Fixed-capacity ring buffer of numeric rows for real-time charts.
export class Ring<T> {
  private items: T[] = [];
  constructor(public capacity: number) {}
  push(v: T): void {
    this.items.push(v);
    if (this.items.length > this.capacity) this.items.splice(0, this.items.length - this.capacity);
  }
  toArray(): T[] {
    return this.items.slice();
  }
  get length(): number {
    return this.items.length;
  }
  clear(): void {
    this.items = [];
  }
  last(): T | undefined {
    return this.items[this.items.length - 1];
  }
}

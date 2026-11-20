"""Derived CSV and person timeline reports from the frame-level JSON summary."""
from __future__ import annotations
import csv, io


def person_statistics(summary: dict) -> list[dict]:
    tracks = {}
    fps = float(summary.get('fps') or 0)
    for frame in summary.get('frames', []):
        ts = float(frame.get('timestamp_seconds') or 0)
        frame_number = frame.get('frame_number')
        for pair in frame.get('associations', []):
            pid = str(pair.get('person_track_id'))
            item = tracks.setdefault(pid, {'person_track_id': int(pid), 'first_seen_seconds': ts,
                'last_seen_seconds': ts, 'associated_frames': 0, 'mean_confidence': 0.0, '_sum': 0.0, '_frames': set()})
            item['last_seen_seconds'] = ts
            if frame_number not in item['_frames']:
                item['associated_frames'] += 1
                item['_frames'].add(frame_number)
            item['_sum'] += float(pair.get('confidence', pair.get('association_score', 0)))
    out=[]
    for item in tracks.values():
        item['duration_seconds'] = round(max(0, item['last_seen_seconds']-item['first_seen_seconds']) + (1/fps if fps else 0), 3)
        item['mean_confidence'] = round(item.pop('_sum') / max(1,item['associated_frames']), 3)
        item.pop('_frames', None)
        out.append(item)
    return sorted(out, key=lambda x: x['person_track_id'])


def csv_report(summary: dict) -> str:
    stream = io.StringIO()
    fields = ['frame_number','timestamp_seconds','person_track_id','phone_track_id','association_score','confidence']
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for frame in summary.get('frames', []):
        for pair in frame.get('associations', []):
            writer.writerow({'frame_number':frame.get('frame_number'), 'timestamp_seconds':frame.get('timestamp_seconds'), **pair})
    return stream.getvalue()

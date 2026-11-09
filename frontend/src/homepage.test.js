import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { HomePage } from './pages/HomePage.tsx';

test('홈 안내 영역에 서비스 안내와 네 가지 기능을 보여준다', () => {
  const markup = renderToStaticMarkup(
    React.createElement(HomePage, { onStart: () => {} }),
  );

  assert.match(markup, /배편부터 문화행사까지, 나들이를 한 번에/);
  assert.match(markup, /운항 정보와 서울시 문화행사 정보/);
  assert.match(markup, /한강버스 연계/);
  assert.match(markup, /문화행사 연계/);
  assert.match(markup, /AI 맞춤 코스/);
  assert.match(markup, /간편한 공유/);
});

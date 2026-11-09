import { CalendarIcon, ShareIcon, ShipIcon, SparkIcon, WarningIcon } from "../components/icons";

export interface HomePageProps {
  onStart: () => void;
}

export function HomePage({ onStart }: HomePageProps) {
  return (
    <section className="landing-screen" aria-labelledby="landing-title">
      <div className="landing-hero">
        <img
          className="landing-hero-image"
          src="/images/hangang-planner-hero.png"
          alt="서울 도심과 한강을 배경으로 운항하는 한강버스 일러스트"
        />
        <div className="landing-hero-shade" aria-hidden="true" />
        <div className="landing-copy">
          <p className="eyebrow">SEOUL · RIVER · CULTURE</p>
          <h1 id="landing-title">
            한강버스와 함께하는
            <br />
            AI 맞춤 나들이 코스
          </h1>
          <p>
            출발 선착장과 오늘의 배편을 고르고
            <br />
            관심 있는 문화행사와 함께 한강 나들이를 준비해 보세요.
          </p>
          <button className="primary-button landing-start" type="button" onClick={onStart}>
            나들이 시작하기 <span aria-hidden="true">→</span>
          </button>
        </div>
      </div>

      <aside className="operating-notice" aria-labelledby="operating-notice-title">
        <div className="operating-notice-summary">
          <span className="operating-notice-icon"><ShipIcon /></span>
          <div>
            <p className="operating-notice-eyebrow">한강버스 운항 안내</p>
            <h2 id="operating-notice-title"><span>연중무휴</span> 운항</h2>
          </div>
        </div>
        <div className="operating-notice-details">
          <p>
            <CalendarIcon />
            <span>월~금·주말·공휴일을 포함해 매일 운항합니다.</span>
          </p>
          <p className="operating-notice-weather">
            <WarningIcon />
            <span>강풍·비바람 등 기상 상황에 따라 안전 운항을 위해 일시 중단될 수 있습니다.</span>
          </p>
        </div>
      </aside>

      <section className="landing-guide" id="guide" aria-labelledby="guide-title">
        <header className="landing-guide-heading">
          <p className="eyebrow">YOUR DAY OUT</p>
          <h2 id="guide-title">배편부터 문화행사까지, 나들이를 한 번에</h2>
        </header>
        <p className="landing-guide-note" role="note">
          <span className="landing-guide-note-icon" aria-hidden="true">i</span>
          <span>
            AI가 한강버스 운항 정보와 서울시 문화행사 정보를 바탕으로 이동·관람 동선을 고려한 나들이 코스 1개를 추천해요. 추천 결과가 마음에 들지 않으면 최대 2회까지 다시 생성할 수 있어요.
          </span>
        </p>
        <ul className="landing-guide-features" aria-label="서비스 주요 기능">
          <li className="landing-guide-feature">
            <span className="landing-guide-feature-icon"><ShipIcon /></span>
            <div>
              <h3>한강버스 연계</h3>
              <p>선택한 구간의 운항 정보를 반영해요.</p>
            </div>
          </li>
          <li className="landing-guide-feature">
            <span className="landing-guide-feature-icon"><CalendarIcon /></span>
            <div>
              <h3>문화행사 연계</h3>
              <p>서울시 문화행사 정보를 함께 확인해요.</p>
            </div>
          </li>
          <li className="landing-guide-feature">
            <span className="landing-guide-feature-icon"><SparkIcon /></span>
            <div>
              <h3>AI 맞춤 코스</h3>
              <p>이동·관람 시간을 고려해 일정을 제안해요.</p>
            </div>
          </li>
          <li className="landing-guide-feature">
            <span className="landing-guide-feature-icon"><ShareIcon /></span>
            <div>
              <h3>간편한 공유</h3>
              <p>링크로 공유하거나 PDF로 저장해요.</p>
            </div>
          </li>
        </ul>
      </section>
    </section>
  );
}
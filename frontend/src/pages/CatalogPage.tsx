import { CatalogSections } from "../components/CatalogSections";

export function CatalogPage() {
  return (
    <section className="catalog-page" aria-labelledby="catalog-page-heading">
      <header className="catalog-page-heading">
        <p className="eyebrow">SEOUL CULTURE · PLACES</p>
        <h1 id="catalog-page-heading">서울의 행사와 장소를 둘러보세요</h1>
        <p>
          서울 전역의 문화행사와 문화·휴식 공간 목록입니다. 나들이 코스 추천과는
          별도로 살펴볼 수 있어요.
        </p>
      </header>
      <CatalogSections />
    </section>
  );
}

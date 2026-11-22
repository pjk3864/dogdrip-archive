# 개드립 인기글 아카이브 - 인수인계 문서

마지막 정리: 2026-08-21

이 문서는 다른 컴퓨터에서 Codex 또는 다른 AI에게 현재 프로젝트를 이어서 작업시키기 위한 설명서다. 토큰 값은 절대 이 문서나 코드에 적지 않는다.

## 1. 서비스 주소와 저장소

- 공개 아카이브: <https://pjk3864.github.io/dogdrip-archive/>
- GitHub 저장소: <https://github.com/pjk3864/dogdrip-archive>
- 로컬 작업 폴더: `C:\Users\pjk38\Desktop\Codex\개드립 프로젝트\dogdrip_bot`

GitHub Pages에 한 번 올라간 아카이브는 PowerShell이나 개인 PC가 꺼져도 다른 컴퓨터와 휴대폰에서 볼 수 있다. PowerShell은 새 글을 수집하고 GitHub에 업로드할 때만 필요하다.

## 2. 현재 목표와 동작

`bot.py`는 개드립 인기글을 개인 GitHub Pages 아카이브로 보관한다.

- 첫 화면은 제목 목록이다.
- 목록은 한 페이지에 20개씩 표시된다.
- 글을 누르면 저장된 본문, 보관 당시의 댓글, 로컬로 복사된 미디어를 본다.
- 읽은 글은 방문자 각자의 브라우저 `localStorage`에만 기록되어 흐린 색으로 표시된다.
- 사이트는 다크 UI다.
- 이미지·영상 파일은 15MB 이하일 때만 GitHub 저장소의 `assets/`에 복사한다. 더 큰 파일은 원본 주소를 유지한다.
- GitHub Pages 한도 때문에 저장소 크기를 계속 확인해야 한다. 현재 저장소 크기는 약 349MB였고, 400개 수준은 약 800MB로 예상된다. Pages는 배포 사이트 1GB 제한이 있다.

## 3. 파일 구조

```text
dogdrip_bot/
  bot.py                 # 수집·HTML 생성·GitHub 업로드 프로그램
  requirements.txt       # Python 패키지 목록
  manual_urls.txt        # 선택 사항: 사람이 브라우저에서 수집한 글 주소 목록

GitHub 저장소 루트/
  index.html             # 목록 첫 페이지
  page-2.html ...        # 이후 목록 페이지
  posts/<글ID>.html      # 글별 아카이브 페이지
  assets/<글ID>/...      # 보관된 이미지·영상
  archive.json           # 보관 글 메타데이터
```

## 4. 다른 컴퓨터에서 설치하기

1. GitHub 저장소를 내려받는다.

   ```powershell
   git clone https://github.com/pjk3864/dogdrip-archive.git
   cd dogdrip-archive\dogdrip_bot
   ```

2. Python 3.11과 Google Chrome을 설치한다.

3. 필요한 패키지를 설치한다.

   ```powershell
   py -3.11 -m pip install -r requirements.txt
   ```

4. GitHub Classic Personal Access Token을 준비한다. 권한은 `repo`가 필요하다. 토큰은 코드나 GitHub 저장소에 저장하지 말고 현재 PowerShell 창의 환경 변수로만 넣는다.

   ```powershell
   $env:GITHUB_TOKEN = "본인_토큰"
   ```

5. 실행한다.

   ```powershell
   py -3.11 bot.py
   ```

새 PowerShell 창을 열면 `GITHUB_TOKEN`을 다시 설정해야 한다.

## 5. 목록 수집 방식

개드립은 Python `requests`로 인기글 목록을 깊게 읽을 때 10페이지부터 403을 반환하는 경우가 있다. 따라서 현재 코드는 인기글 **목록 페이지**를 Selenium이 연 Chrome에서 읽도록 되어 있다. 본문·댓글·미디어는 Python 요청으로 수집한다.

현재 기본값:

- 기존 보관 글과 중복되는 글 ID는 건너뜀
- 기본 보관 목표: 최소 400개
- 목록은 최대 20페이지까지 확인
- 최신 인기글이 새로 생기면 기존 글을 삭제하지 않고 추가
- 댓글은 10개 제한 없이, 수집 시점에 페이지에 보이는 댓글 전부를 보관

## 6. 수동 주소 목록으로 가져오기

자동 목록 수집이 10페이지 이후에서 막히면 `manual_urls.txt`를 사용한다. 이 파일이 존재하고 내용이 있으면, 봇은 목록 페이지를 자동 탐색하지 않고 파일에 적힌 주소만 처리한다.

### 브라우저에서 주소 파일 만들기

개드립 인기글 10페이지를 웨일 또는 Chrome으로 열고 `F12` → `Console`에서 아래 코드를 실행한다. 코드는 10~25페이지의 글 주소를 모아 `manual_urls.txt` 파일을 다운로드한다.

```javascript
(async () => {
  const urls = new Set();
  for (let page = 10; page <= 25; page++) {
    const response = await fetch(`/?mid=dogdrip&sort_index=popular&page=${page}`);
    if (!response.ok) continue;
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    doc.querySelectorAll("a.ed.title-link[data-document-srl]").forEach((a) => {
      urls.add(new URL(a.href, location.origin).href);
    });
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  const file = new Blob([[...urls].join("\n")], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(file);
  link.download = "manual_urls.txt";
  link.click();
  URL.revokeObjectURL(link.href);
})();
```

다운로드된 `manual_urls.txt`를 `dogdrip_bot` 폴더에 옮긴 뒤 `py -3.11 bot.py`를 실행한다. 이미 보관한 글은 자동으로 건너뛴다.

## 7. 현재 문제와 주의점

- 개드립이 429(Too Many Requests)를 반환하면 현재 실행은 `Ctrl + C`로 중단한다.
- 이미 성공적으로 GitHub에 올라간 글은 각 글별 커밋으로 남는다. 중간에 중단해도 성공분은 사라지지 않는다.
- 429가 발생했다면 최소 30분 정도 기다린 뒤 다시 시도한다. 같은 IP에서 즉시 재시도하면 제한이 길어질 수 있다.
- 현재 코드는 본문 요청 사이에 약 1.5초 간격을 두고, 429가 나오면 60초 후 재시도한다. 그래도 계속 429면 중단하는 편이 낫다.
- `manual_urls.txt`는 공개 글 주소만 담지만, 토큰과 달리 비밀값은 아니다. 그래도 수동 작업용 파일이므로 GitHub에 올릴 필요는 없다.

## 8. 다른 AI에게 전달할 요청 예시

다른 컴퓨터에서 이 문서를 열고 AI에게 아래처럼 요청하면 된다.

```text
PROJECT_HANDOFF.md와 dogdrip_bot/bot.py를 먼저 읽어줘.
개드립 인기글 아카이브 프로젝트를 이어서 작업하려고 해.
GitHub 토큰은 코드에 넣지 말고 GITHUB_TOKEN 환경 변수만 사용해.
개드립이 403/429를 주면 대량 재시도하지 말고 현재 진행 상태를 보존해줘.
```

## 9. 보안 체크

- GitHub 토큰을 채팅, Markdown 파일, `bot.py`, GitHub 커밋에 넣지 않는다.
- 이미 채팅에 노출된 적 있는 토큰은 GitHub에서 폐기하고 새 토큰을 발급한다.
- `GITHUB_TOKEN` 환경 변수는 현재 PowerShell 창을 닫으면 사라진다.


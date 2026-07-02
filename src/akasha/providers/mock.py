from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MockScene:
    provider: str
    source_id: str
    product_id: str
    acquisition_date: str


class MockProvider:
    provider_adapter = "mock"

    def search(self, *, source_id: str, date_start: str, date_end: str) -> MockScene:
        return MockScene(
            provider=self.provider_adapter,
            source_id=source_id,
            product_id=f"MOCK_{source_id}_{date_start}_{date_end}",
            acquisition_date=date_start,
        )

    def package_bytes(self, scene: MockScene) -> bytes:
        return (
            "akasha mock provider package\n"
            f"provider={scene.provider}\n"
            f"source_id={scene.source_id}\n"
            f"product_id={scene.product_id}\n"
            f"acquisition_date={scene.acquisition_date}\n"
        ).encode()

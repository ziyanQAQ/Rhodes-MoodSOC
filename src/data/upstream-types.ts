/**
 * ArknightsGameData 原始 JSON 的最小字段映射，只声明当前目录生成器实际读取的内容。
 * 带 Raw 语义的数值保持上游原值，不能据此直接推断游戏公式或单位换算。
 */
export interface SourceFileFingerprint {
  readonly path: string;
  readonly sha256: string;
  readonly bytes: number;
}

export interface UpstreamSourceMetadata {
  // 本地下载包可能没有 Git 元数据，因此使用原始文件指纹作为可追溯版本依据。
  readonly kind: "local-archive";
  readonly root: string;
  readonly files: {
    readonly buildingData: SourceFileFingerprint;
    readonly characterTable: SourceFileFingerprint;
    readonly gamedataConst: SourceFileFingerprint;
    readonly handbookTeamTable: SourceFileFingerprint;
  };
}

export interface UpstreamBuffCondition {
  readonly phase: string;
  readonly level: number;
}

export interface UpstreamBuildingCharacter {
  readonly charId: string;
  readonly maxManpower: number;
  readonly buffChar: readonly {
    readonly buffData: readonly {
      readonly buffId: string;
      readonly cond: UpstreamBuffCondition;
    }[];
  }[];
}

export interface UpstreamBuff {
  readonly buffId: string;
  readonly buffName: string;
  readonly buffCategory: string;
  readonly roomType: string;
  readonly description: string;
  readonly efficiency: number;
  readonly targets: readonly string[];
}

export interface UpstreamRoomPhase {
  readonly unlockCondId: string;
  readonly electricity: number;
  readonly maxStationedNum: number;
  readonly manpowerCost: number;
}

export interface UpstreamRoom {
  readonly id: string;
  readonly name: string;
  readonly description: string | null;
  readonly maxCount: number;
  readonly category: string;
  readonly size: { readonly row: number; readonly col: number };
  readonly phases: readonly UpstreamRoomPhase[];
}

export interface UpstreamBuildingData {
  readonly comfortManpowerRecoverFactor: number;
  readonly manpowerDisplayFactor: number;
  readonly tiredApThreshold: number;
  readonly laborRecoverTime: number;
  readonly rooms: Readonly<Record<string, UpstreamRoom>>;
  readonly dormData: {
    readonly phases: readonly {
      readonly manpowerRecover: number;
      readonly decorationLimit: number;
    }[];
  };
  readonly chars: Readonly<Record<string, UpstreamBuildingCharacter>>;
  readonly buffs: Readonly<Record<string, UpstreamBuff>>;
}

export interface UpstreamCharacter {
  readonly name: string;
  readonly nationId: string | null;
  readonly groupId: string | null;
  readonly teamId: string | null;
}

export interface UpstreamCatalogRaw {
  readonly source: UpstreamSourceMetadata;
  readonly buildingData: UpstreamBuildingData;
  readonly characterTable: Readonly<Record<string, UpstreamCharacter>>;
  readonly gamedataConst: Readonly<Record<string, unknown>>;
  readonly handbookTeamTable: Readonly<Record<string, { readonly powerId: string; readonly powerName: string }>>;
}

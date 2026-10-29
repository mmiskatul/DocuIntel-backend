import { IsBoolean, IsNotEmpty, IsObject, IsOptional, IsString } from 'class-validator';

export class CorrectionDto {
  @IsObject()
  @IsNotEmpty()
  corrections: Record<string, any>;

  @IsBoolean()
  @IsOptional()
  confirm?: boolean = false;

  @IsString()
  @IsOptional()
  reviewer?: string;
}


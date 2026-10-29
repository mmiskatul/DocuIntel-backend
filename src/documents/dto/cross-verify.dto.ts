import { IsNotEmpty, IsNumber, IsOptional, IsString } from 'class-validator';

export class CrossVerifyDto {
  @IsString()
  @IsNotEmpty()
  document_id_a: string;

  @IsString()
  @IsNotEmpty()
  document_id_b: string;

  @IsNumber()
  @IsOptional()
  tolerance?: number = 0.02;
}

